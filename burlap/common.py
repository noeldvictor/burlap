from __future__ import with_statement, print_function

import os
import re
import sys
import types
import copy
import tempfile
import time
import importlib
import warnings
import glob
import yaml
import pipes
import json
import getpass
import inspect
import subprocess
from collections import namedtuple, OrderedDict
from pprint import pprint
from datetime import date
import six

from fabric.api import (
    env,
    local,
    put as __put,
    get as __get,
    require,
    run as _run,
    settings,
    sudo as _sudo,
    cd,
    hide,
    runs_once,
    local as _local,
)
from fabric.contrib import files
from fabric import state
import fabric.api

from .constants import *
from .utils import get_file_hash
from .shelf import Shelf
from .decorators import task

if hasattr(fabric.api, '_run'):
    _run = fabric.api._run
    
if hasattr(fabric.api, '_sudo'):
    _sudo = fabric.api._sudo

BURLAP_COMMAND_PREFIX = int(os.environ.get('BURLAP_COMMAND_PREFIX', '1'))

OS = namedtuple('OS', ['type', 'distro', 'release'])

ROLE_DIR = env.ROLES_DIR = 'roles'

if 'services' not in env:

    env.services = []
    env.confirm_deployment = False
    env.is_local = None
    env.base_config_dir = '.'
    env.src_dir = 'src' # The path relative to fab where the code resides.
    env.sites = {} # {site:site_settings}

env[SITE] = None
env[ROLE] = None

# If true, prevents run() from executing its command.
_dryrun = False

# If true, causes output of more debugging info.
_verbose = False

_show_command_output = True

required_system_packages = type(env)() # {service:{os:[packages]}
required_python_packages = type(env)() # {service:{os:[packages]}
required_ruby_packages = type(env)() # {service:{os:[packages]}

service_configurators = type(env)() # {service:[func]}
service_pre_deployers = type(env)() # {service:[func]}
service_pre_db_dumpers = type(env)() # {service:[func]}
service_deployers = type(env)() # {service:[func]}
service_post_deployers = type(env)() # {service:[func]}
service_post_db_loaders = type(env)() # {service:[func]}
service_restarters = type(env)() # {service:[func]}
service_stoppers = type(env)() # {service:[func]}
services = {} # {name: service_obj}

manifest_recorder = type(env)() #{component:[func]}
manifest_comparer = type(env)() #{component:[func]}
manifest_deployers = type(env)() #{component:[func]}
manifest_deployers_befores = type(env)() #{component:[pending components that must be run first]}
#manifest_deployers_afters = type(env)() #{component:[pending components that must be run last]}
manifest_deployers_takes_diff = type(env)()

_post_import_modules = set()

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def start_error():
    print(Colors.FAIL)

def end_error():
    print(Colors.ENDC)

def print_fail(s, file=None):
    print(Colors.FAIL + str(s) + Colors.ENDC, file=file or sys.stderr)

def print_success(s, file=None):
    print(Colors.OKGREEN + str(s) + Colors.ENDC, file=file or sys.stdout)

def create_module(name, code=None):
    """
    Dynamically creates a module with the given name.
    """
    import sys, imp

    if name not in sys.modules:
        sys.modules[name] = imp.new_module(name)

    module = sys.modules[name]
    
    if code:
        print('executing code for %s: %s' % (name, code))
        exec(code in module.__dict__)
        exec("from %s import %s" % (name, '*'))

    return module

#http://www.saltycrane.com/blog/2010/09/class-based-fabric-scripts-metaprogramming-hack/
#http://stackoverflow.com/questions/3799545/dynamically-importing-python-module/3799609#3799609
def add_class_methods_as_module_level_functions_for_fabric(instance, module_name, method_name, module_alias=None):
    '''
    Utility to take the methods of the instance of a class, instance,
    and add them as functions to a module, module_name, so that Fabric
    can find and call them. Call this at the bottom of a module after
    the class definition.
    '''
    import imp
    from .decorators import task_or_dryrun
    
    # get the module as an object
    module_obj = sys.modules[module_name]

    module_alias = re.sub('[^a-zA-Z0-9]+', '', module_alias or '')

    # Iterate over the methods of the class and dynamically create a function
    # for each method that calls the method and add it to the current module
    # NOTE: inspect.ismethod actually executes the methods?!
    #for method in inspect.getmembers(instance, predicate=inspect.ismethod):
    
    method_obj = getattr(instance, method_name)

    if not method_name.startswith('_'):
        
        # get the bound method
        func = getattr(instance, method_name)
        
#         if module_name == 'buildbot' or module_alias == 'buildbot':
#             print('-'*80)
#             print('module_name:', module_name)
#             print('method_name:', method_name)
#             print('module_alias:', module_alias)
#             print('module_obj:', module_obj)
#             print('func.module:', func.__module__)
        
        # Convert executable to a Fabric task, if not done so already.
        if not hasattr(func, 'is_task_or_dryrun'):
            func = task_or_dryrun(func)

        if module_name == module_alias \
        or (module_name.startswith('satchels.') and module_name.endswith(module_alias)):

            # add the function to the current module
            setattr(module_obj, method_name, func)
            
        else:
            
            # Dynamically create a module for the virtual satchel.
            _module_obj = module_obj
            module_obj = create_module(module_alias)
            setattr(module_obj, method_name, func)
            _post_import_modules.add(module_alias)
        
        fabric_name = '%s.%s' % (module_alias or module_name, method_name)
        func.wrapped.__func__.fabric_name = fabric_name
        
        return func

def add_deployer(event, func, before=[], after=[], takes_diff=False):
    event = event.strip().upper()
    
    manifest_deployers.setdefault(event, [])
    if func not in manifest_deployers[event]:
        manifest_deployers[event].append(func)
    
    manifest_deployers_befores.setdefault(event, [])
    manifest_deployers_befores[event].extend(map(str.upper, before))
    
    for _c in after:
        _c = _c.strip().upper()
        manifest_deployers_befores.setdefault(_c, [])
        manifest_deployers_befores[_c].append(event)
    
    manifest_deployers_takes_diff[func] = takes_diff

def resolve_deployer(func_name):
    print('resolve deployer:', func_name)
    
    if '.' in func_name:
        mod_name, func_name = func_name.split('.')
    else:
        mod_name = 'fabfile'
        
    if mod_name.upper() in all_satchels:
        ret = all_satchels[mod_name.upper()].configure
    else:
        ret = getattr(importlib.import_module(mod_name), func_name)
        
    return ret

class Deployer(object):
    """
    Represents a task that must be run to update a service after a configuration change
    has been made.
    """
    
    def __init__(self, func, before=None, after=None, takes_diff=False):
        self.func = func
        self.before = before or []
        self.after = after or []
        self.takes_diff = takes_diff

def get_class_module_name(self):
    name = self.__module__
    if name == '__main__':
        filename = sys.modules[self.__module__].__file__
        name = os.path.splitext(os.path.basename(filename))[0]
    return name

class _EnvProxy(object):
    """
    Filters a satchel's access to the enviroment object.
    
    Allows referencing of environment variables without explicitly specifying
    the Satchel's namespace.
    
    For example, instead of:
    
        env.satchelname_variable = 123
    
    you can use:
        
        self.env.variable = 123 
    """
    
    def __init__(self, satchel):
        self.satchel = satchel
    
    def __contains__(self, k):
#         if k in ('satchel',):
#             return k in super(_EnvProxy, self).__contains__(k)
        k = (self.satchel.env_prefix + k)
        return k in env
    
    def __getattr__(self, k):
        if k in ('satchel',):
            return super(_EnvProxy, self).__getattr__(k)
        return env[self.satchel.env_prefix + k]
        
    def __setattr__(self, k, v):
        if k in ('satchel',):
            return super(_EnvProxy, self).__setattr__(k, v)
        env[self.satchel.env_prefix + k] = v

SATCHEL_NAME_PATTERN = re.compile(r'^[a-z][a-z0-9]*$')

all_satchels = {}

def assert_valid_satchel(name):
    name = name.strip().upper()
    assert name in all_satchels
    return name

class Renderer(object):
    """
    Base convenience wrapper around command executioners.
    """
    
    env_type = None
    
    def __init__(self, obj, lenv=None):
        # Satchel instance.
        self.obj = obj
        # Copy the environment dictionary so we don't modify the original.
        self.lenv = type(env)(obj.lenv if lenv is None else lenv)
        self.genv = type(env)(obj.genv)
    
    def __getattr__(self, attrname):
        
        # Alias .env to the default type.
        if attrname == 'env':
            attrname = self.env_type
        
        if attrname in ('obj', 'lenv', 'genv', 'env_type'):
            return super(LocalRenderer, self).__getattribute__(attrname)
        
        def wrap(func):
            
            def _wrap(cmd, *args, **kwargs):
                cmd = cmd.format(**getattr(self, self.env_type))
                return func(cmd, *args, **kwargs)
            
            return _wrap
        
        def wrap2(func):
            # For non-command functions, just pass-through.
            
            def _wrap(*args, **kwargs):
                return func(*args, **kwargs)
            
            return _wrap
            
        ret = getattr(self.obj, attrname)
        
        # If we're calling a command executor, wrap it so that it automatically formats
        # the command string using our preferred environment dictionary when called.
        if attrname.startswith('local') \
        or attrname.startswith('run') \
        or attrname.startswith('comment') \
        or attrname.startswith('sudo'):
            ret = wrap(ret)
        elif attrname.startswith('put') \
        or attrname.startswith('append') \
        or attrname.startswith('sed'):
            ret = wrap2(ret)
            
        return ret
        
class LocalRenderer(Renderer):
    
    env_type = 'lenv'

class GlobalRenderer(Renderer):
    
    env_type = 'genv'
    
class Satchel(object):
    """
    Represents a base unit of functionality that is deployed and maintained on one
    or more a target servers.
    """
    
    # This will be used to uniquely identify this unit of functionality.
    name = None
    
    # This is the list of Fabric tasks exposed by the instance.
    tasks = []
    
    required_system_packages = {
        #OS: [package1, package2, ...],
    }
    
    # These files will have their changes tracked.
    templates = []
    
    def __init__(self):
        assert self.name, 'A name must be specified.'
        self.name = self.name.strip().lower()
        
        assert SATCHEL_NAME_PATTERN.findall(self.name), 'Invalid name: %s' % self.name
        
        self._os_version_cache = {} # {host:info}
        
        all_satchels[self.name.upper()] = self
        
        # Global environment.
        self.genv = env
        
        self._requires_satchels = set()
        
        self.env = _EnvProxy(self)
        
        self.files = files
        
        self.settings = settings
        
        _prefix = '%s_enabled' % self.name
        if _prefix not in env:
            env[_prefix] = True
            self.set_defaults()
        
        manifest_recorder[self.name] = self.record_manifest
                
        super(Satchel, self).__init__()
        
        # Register service commands.
        if self.required_system_packages:
            required_system_packages[self.name.upper()] = self.required_system_packages
        
        # Add built-in tasks.
        if 'install_packages' not in self.tasks:
            self.tasks += ('install_packages',)
        if 'configure' not in self.tasks:
            self.tasks += ('configure',)
        
        # Register select instance methods as Fabric tasks.
        for task_name in self.get_tasks():
            task = add_class_methods_as_module_level_functions_for_fabric(
                instance=self,
                module_name=get_class_module_name(self),
                method_name=task_name,
                module_alias=self.name,
            )
            
            # If task is marked as a deployer, then add it to the deployer list.
            if hasattr(task.wrapped, 'is_deployer') or task_name == 'configure':
                add_deployer(
                    event=self.name,
                    func=task.wrapped.fabric_name,#deployer.func,
                    before=getattr(task.wrapped, 'deploy_before', []),#deployer.before,
                    after=getattr(task.wrapped, 'deploy_after', []),#deployer.after,
                    takes_diff=getattr(task.wrapped, 'deployer_takes_diff', False))
                
        deployers = self.get_deployers()
        if deployers:
            for deployer in deployers:
                assert isinstance(deployer, Deployer), 'Invalid deployer "%s".' % deployer
                add_deployer(
                    event=self.name,
                    func=deployer.func,
                    before=deployer.before,
                    after=deployer.after,
                    takes_diff=deployer.takes_diff)
    
    def get_tasks(self):
        """
        Returns an ordered list of all task names.
        """
        tasks = set(self.tasks)#DEPRECATED
        for _name in dir(self):
            # Skip properties so we don't accidentally execute any methods.
            if isinstance(getattr(type(self), _name, None), property):
                continue
            attr = getattr(self, _name)
            if hasattr(attr, '__call__') and getattr(attr, 'is_task', False):
                tasks.add(_name)
        return sorted(tasks)
    
    @task
    def list_tasks(self):
        for _task in self.get_tasks():
            print(_task)
    
    @property
    def local_renderer(self):
        return LocalRenderer(self)
    
    @property
    def global_renderer(self):
        return GlobalRenderer(self)
        
    @property
    def all_satchels(self):
        return all_satchels
    
    def define_cron_job(self, template, script_path, command=None, name='default', perms='600'):
        if 'cron' not in self.env:
            self.env.cron = type(env)()
        self.env.cron[name] = type(env)()
        self.env.cron[name].template = '%s/%s' % (self.name, template)
        self.env.cron[name].script_path = script_path
        self.env.cron[name].command = command
        self.env.cron[name].perms = '600'
        self.templates = list(self.templates)
        self.templates.append(template)
    
    def install_cron_job(self, name='default', extra=None):
        assert name in self.env.cron
        
        data = self.env.cron[name]
        data.update(extra or {})
    
        self.install_script(
            local_path=data.template,
            remote_path=data.script_path,
            render=True,
            extra=data,
        )
        
        r = self.local_renderer
            
        r.sudo('chown root:root %s' % data.script_path)
        
        # Must be 600, otherwise gives INSECURE MODE error.
        # http://unix.stackexchange.com/questions/91202/cron-does-not-print-to-syslog
        r.sudo('chmod %s %s' % (data.perms, data.script_path))#env.put_remote_path)
        r.sudo('service cron restart')

    def uninstall_cron_job(self, name):
        assert name in self.cron
        
    def pc(self, *args, **kwargs):
        return pc(*args, **kwargs)
    
    def requires_satchel(self, satchel):
        self._requires_satchels.add(satchel.name.lower())
    
    def check_satchel_requirements(self):
        lst = []
        lst.extend(self.genv.get('services') or [])
        lst.extend(self.genv.get('satchels') or [])
        lst = [_.lower() in lst]
        for req in self._requires_satchels:
            req = req.lower()
            assert req in lst
    
    @property
    def lenv(self):
        """
        Returns a version of env filtered to only include the variables in our namespace.
        """
        _env = type(env)()
        for _k, _v in six.iteritems(env):
            if _k.startswith(self.name+'_'):
                _env[_k[len(self.name)+1:]] = _v
        return _env
    
    @property
    def env_prefix(self):
        return '%s_' % self.name
    
    @property
    def packager(self):
        return get_packager()
    
    @property
    def os_version(self):
        hs = env.host_string
        if hs not in self._os_version_cache:
            self._os_version_cache[hs] = get_os_version()
        return self._os_version_cache[hs]
    
    def sleep(self, seconds):
        if self.dryrun:
            cmd = 'sleep %s' % seconds
            if BURLAP_COMMAND_PREFIX:
                print('%s local: %s' % (render_command_prefix(), cmd))
            else:
                print(cmd)
        else:
            time.sleep(seconds)
    
    def _local(self, *args, **kwargs):
        return _local(*args, **kwargs)
    
    def render_to_string(self, *args, **kwargs):
        return render_to_string(*args, **kwargs)
    
    def reboot_or_dryrun(self, *args, **kwargs):
        """
        Reboots the server and waits for it to come back.
        """
        warnings.warn('Use self.run() instead.', DeprecationWarning, stacklevel=2)
        self.reboot(*args, **kwargs)
    
    def reboot(self, *args, **kwargs):
        """
        Reboots the server and waits for it to come back.
        """
        reboot_or_dryrun(*args, **kwargs)
    
    def write_to_file(self, *args, **kwargs):
        return write_to_file(*args, **kwargs)
    
    def find_template(self, template):
        return find_template(template)
    
    def get_template_contents(self, template):
        return get_template_contents(template)
    
    def install_script(self, *args, **kwargs):
        return install_script(*args, **kwargs)
    
    def install_packages(self):
        os_version = self.os_version # OS(type=LINUX, distro=UBUNTU, release='14.04')
#         print('os_version:', os_version)
        req_packages = self.required_system_packages
        patterns = [
            (os_version.type, os_version.distro, os_version.release),
            (os_version.distro, os_version.release),
            (os_version.type, os_version.distro),
            (os_version.distro,),
            os_version.distro,
        ]
#         print('req_packages:', req_packages)
        package_list = None
        for pattern in patterns:
#             print('pattern:', pattern)
            if pattern in req_packages:
                package_list = req_packages[pattern]
                break
#         print('package_list:', package_list)
        if package_list:
            package_list_str = ' '.join(package_list)
            if os_version.distro == UBUNTU:
                self.sudo_or_dryrun('apt-get update --fix-missing; apt-get install --yes %s' % package_list_str)
            elif os_version.distro == DEBIAN:
                self.sudo_or_dryrun('apt-get update --fix-missing; apt-get install --yes %s' % package_list_str)
            elif os_version.distro == FEDORA:
                self.sudo_or_dryrun('yum install --assumeyes %s' % package_list_str)
            else:
                raise NotImplementedError('Unknown distro: %s' % os_version.distro)
    
    def purge_packages(self):
        os_version = self.os_version # OS(type=LINUX, distro=UBUNTU, release='14.04')
#         print('os_version:', os_version)
        req_packages = self.required_system_packages
        patterns = [
            (os_version.type, os_version.distro, os_version.release),
            (os_version.distro, os_version.release),
            (os_version.type, os_version.distro),
            (os_version.distro,),
            os_version.distro,
        ]
#         print('req_packages:', req_packages)
        package_list = None
        for pattern in patterns:
#             print('pattern:', pattern)
            if pattern in req_packages:
                package_list = req_packages[pattern]
                break
#         print('package_list:', package_list)
        if package_list:
            package_list_str = ' '.join(package_list)
            if os_version.distro == UBUNTU:
                self.sudo_or_dryrun('apt-get purge %s' % package_list_str)
            elif os_version.distro == FEDORA:
                self.sudo_or_dryrun('yum remove %s' % package_list_str)
            else:
                raise NotImplementedError('Unknown distro: %s' % os_version.distro)
    
    def set_defaults(self):
        # Override to specify custom defaults.
        pass
    
    def render_to_file(self, *args, **kwargs):
        return render_to_file(*args, **kwargs)
    
    def put_or_dryrun(self, *args, **kwargs):
        warnings.warn('Use self.put() instead.', DeprecationWarning, stacklevel=2)
        return put_or_dryrun(*args, **kwargs)
    
    def put(self, *args, **kwargs):
        return put_or_dryrun(*args, **kwargs)
    
    def run_or_dryrun(self, *args, **kwargs):
        warnings.warn('Use self.run() instead.', DeprecationWarning, stacklevel=2)
        return run_or_dryrun(*args, **kwargs)
    
    def run(self, *args, **kwargs):
        return run_or_dryrun(*args, **kwargs)
    
    def local_or_dryrun(self, *args, **kwargs):
        warnings.warn('Use self.local() instead.', DeprecationWarning, stacklevel=2)
        return local_or_dryrun(*args, **kwargs)
    
    def append(self, *args, **kwargs):
        return append_or_dryrun(*args, **kwargs)
    
    def sed(self, *args, **kwargs):
        return sed_or_dryrun(*args, **kwargs)
    
    def local(self, *args, **kwargs):
        return local_or_dryrun(*args, **kwargs)
    
    def sudo_or_dryrun(self, *args, **kwargs):
        return sudo_or_dryrun(*args, **kwargs)
    
    def sudo(self, *args, **kwargs):
        warnings.warn('Use self.sudo() instead.', DeprecationWarning, stacklevel=2)
        return sudo_or_dryrun(*args, **kwargs)
    
    def comment(self, *args):
        print('# ' + (' '.join(map(str, args))))
    
    def print_command(self, *args, **kwargs):
        return print_command(*args, **kwargs)
        
    def record_manifest(self):
        """
        Returns a dictionary representing a serialized state of the service.
        """
        manifest = get_component_settings(self.name)
        
        # Record a signature of each template so we know to redeploy when they change.
        for template in self.templates:
            fqfn = self.find_template('%s/%s' % (self.name, template))
            assert fqfn, 'Unable to find template: %s/%s' % (self.name, template)
            manifest['_%s' % template] = get_file_hash(fqfn)
        
        return manifest
    
    def configure(self):
        """
        The standard method called to apply functionality when the manifest changes.
        """
        raise NotImplementedError

    configure.deploy_before = []
    configure.takes_diff = False #DEPRECATED
    
    #TODO:deprecated, remove?
    def get_deployers(self):
        """
        Returns one or more Deployer instances, representing tasks to run during a deployment.
        """
        #raise NotImplementedError
        
    @property
    def current_manifest(self):
        from burlap import manifest
        return manifest.get_current(name=self.name)
        
    @property
    def last_manifest(self):
        from burlap import manifest
        return manifest.get_last(name=self.name)
        
    @property
    def verbose(self):
        return get_verbose()
        
    @verbose.setter
    def verbose(self, v):
        return set_verbose(v)
        
    @property
    def dryrun(self):
        return get_dryrun()
        
    @dryrun.setter
    def dryrun(self, v):
        return set_dryrun(v)

class Service(object):
    
    name = None
    
    commands = {} # {action: {os_version_distro: command}}
    
    # If true, any warnings or errors encountered during commands will be ignored.
    ignore_errors = False
    
    # This command will be automatically run after every deployment.
    post_deploy_command = None #'restart'
    
    def __init__(self):
        assert self.name
        self.name = self.name.strip().lower()
        service_restarters[self.name.upper()] = [self.restart]
        service_stoppers[self.name.upper()] = [self.stop]
        if self.post_deploy_command:
            service_post_deployers[self.name.upper()] = [getattr(self, self.post_deploy_command)]
            
        super(Service, self).__init__()
        
        services[self.name.strip().upper()] = self
        
        _key = '%s_service_commands' % self.name
        if _key in env:
            self.commands = env[_key]
 
        tasks = (
            'start',
            'stop',
            'restart',
            'enable',
            'disable',
            'status',
            'reload',
        )   
        for task_name in tasks:
            task = add_class_methods_as_module_level_functions_for_fabric(
                instance=self,
                module_name=get_class_module_name(self),
                method_name=task_name,
                module_alias=self.name,
            )
    
    def get_command(self, action):
        os_version = self.os_version # OS(type=LINUX, distro=UBUNTU, release='14.04')
#         print('os_version:', os_version)
        patterns = [
            (os_version.type, os_version.distro, os_version.release),
            (os_version.distro, os_version.release),
            (os_version.type, os_version.distro),
            (os_version.distro,),
            os_version.distro,
        ]
        for pattern in patterns:
            if pattern in self.commands[action]:
                return self.commands[action][pattern]
    
    def enable(self):
        cmd = self.get_command(ENABLE)
        sudo_or_dryrun(cmd)
    
    def disable(self):
        cmd = self.get_command(DISABLE)
        sudo_or_dryrun(cmd)
        
    def restart(self):
        s = {'warn_only':True} if self.ignore_errors else {} 
        with settings(**s):
            cmd = self.get_command(RESTART)
            sudo_or_dryrun(cmd)
        
    def reload(self):
        s = {'warn_only':True} if self.ignore_errors else {} 
        with settings(**s):
            cmd = self.get_command(RELOAD)
            sudo_or_dryrun(cmd)
        
    def start(self):
        s = {'warn_only':True} if self.ignore_errors else {} 
        with settings(**s):
            cmd = self.get_command(START)
            sudo_or_dryrun(cmd)
        
    def stop(self, ignore_errors=True):
        s = {'warn_only':True} if ignore_errors else {} 
        with settings(**s):
            cmd = self.get_command(STOP)
            sudo_or_dryrun(cmd)
        
    def status(self):
        with settings(warn_only=True):
            cmd = self.get_command(STATUS)
            return sudo_or_dryrun(cmd)
            
    def is_running(self):
        status = str(self.status())
        status = re.sub(r'[\s\s]+', ' ', status)
        return 'is running' in status or 'start/running' in status

class ServiceSatchel(Satchel, Service):
    pass

env.hosts_retriever = None
env.hosts_retrievers = type(env)() #'default':lambda hostname: hostname,

env.hostname_translator = 'default'
env.hostname_translators = type(env)()
env.hostname_translators.default = lambda hostname: hostname

env.default_site = None

#env.shell_default_dir_template = '/usr/local/%(app_name)s'
env.shell_default_dir_template = '%(remote_app_src_package_dir)s'
env.shell_interactive_shell = 'export SITE=%(SITE)s; export ROLE=%(ROLE)s; cd %(shell_default_dir)s; /bin/bash -i'

# A list of all site names that should be available on the current host.
env.available_sites = []

# A list of all site names per host.
# {hostname: [sites]}
# If no entry found, will use available_sites.
env.available_sites_by_host = {}

# The command run to determine the percent of disk usage.
env.disk_usage_command = "df -H | grep -vE '^Filesystem|tmpfs|cdrom|none' | awk '{print $5 " " $1}'"

env.post_callbacks = []

env.burlap_data_dir = '.burlap'

def env_hosts_retriever(*args, **kwargs):
    data = {}
    if env.host_hostname:
        data[env.host_hostname] = {}
    return data.items()

def get_hosts_retriever(s=None):
    """
    Given the function name, looks up the method for dynamically retrieving host data.
    """
    s = s or env.hosts_retriever
    #assert s, 'No hosts retriever specified.'
    if not s:
        return env_hosts_retriever
    module_name = '.'.join(s.split('.')[:-1])
    func_name = s.split('.')[-1]
    retriever = getattr(importlib.import_module(module_name), func_name)
    return retriever

def shellquote(s, singleline=True):
    if singleline:
        s = pipes.quote(s)
        s = repr(s)
        s = re.sub(r'^u*[\"\']+', '', s)
        s = re.sub(r'[\"\']+$', '', s)
        s = '"%s"' % s
    else:
        s = '{}'.format(pipes.quote(s))
    return s

def init_burlap_data_dir():
    d = env.burlap_data_dir
    if not os.path.isdir(env.burlap_data_dir):
        os.mkdir(d)

def set_dryrun(dryrun):
    global _dryrun
    _dryrun = bool(int(dryrun or 0))
    if _dryrun:
        state.output.running = False
    else:
        state.output.running = True

def get_dryrun(dryrun=None):
    if dryrun is None or dryrun == '':
        return bool(int(_dryrun or 0))
    return bool(int(dryrun or 0))

def set_verbose(verbose):
    global _verbose
    _verbose = bool(int(verbose or 0))

def get_verbose(verbose=None):
    if verbose is None or verbose == '':
        return bool(int(_verbose or 0))
    return bool(int(verbose or 0))

def set_show(v):
    _show_command_output = bool(int(v))

def get_show():
    return _show_command_output
    
def render_command_prefix():
    extra = {}
    if env.key_filename:
        extra['key'] = env.key_filename
    extra_s = ''
    if extra:
        extra_s = json.dumps(extra)
    s = '[%s@%s%s]' % (env.user, env.host_string, extra_s)
    return s

def print_command(cmd):
    print('[%s@localhost] local: %s' % (getpass.getuser(), cmd))

def append_or_dryrun(*args, **kwargs):
    """
    Wrapper around Fabric's contrib.files.append() to give it a dryrun option.
    
    http://docs.fabfile.org/en/0.9.1/api/contrib/files.html#fabric.contrib.files.append
    """
    dryrun = get_dryrun(kwargs.get('dryrun'))
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
        
    use_sudo = kwargs.get('use_sudo', False)
        
    if dryrun:
        text = args[0] if len(args) >= 1 else kwargs['text']
        filename = args[1] if len(args) >= 2 else kwargs['filename']
        cmd = 'echo "%s" >> %s' % (text, filename)
        cmd_run = 'sudo' if use_sudo else 'run'
        if BURLAP_COMMAND_PREFIX:
            print('%s %s: %s' % (render_command_prefix(), cmd_run, cmd))
        else:
            print(cmd)

def sed_or_dryrun(*args, **kwargs):
    """
    Wrapper around Fabric's contrib.files.sed() to give it a dryrun option.
    
    http://docs.fabfile.org/en/0.9.1/api/contrib/files.html#fabric.contrib.files.sed
    """
    dryrun = get_dryrun(kwargs.get('dryrun'))
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
        
    use_sudo = kwargs.get('use_sudo', False)
        
    if dryrun:
        context = dict(
            filename=args[0] if len(args) >= 1 else kwargs['filename'],
            before=args[1] if len(args) >= 2 else kwargs['before'],
            after=args[2] if len(args) >= 3 else kwargs['after'],
            backup=args[3] if len(args) >= 4 else kwargs.get('backup', '.bak'),
            limit=kwargs.get('limit', ''),
        )
        cmd = 'sed -i{backup} -r -e "/{limit}/ s/{before}/{after}/g {filename}"'.format(**context)
        cmd_run = 'sudo' if use_sudo else 'run'
        if BURLAP_COMMAND_PREFIX:
            print('%s %s: %s' % (render_command_prefix(), cmd_run, cmd))
        else:
            print(cmd)

def local_or_dryrun(*args, **kwargs):
    dryrun = get_dryrun(kwargs.get('dryrun'))
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
        
    assign_to = kwargs.pop('assign_to', None)
    if assign_to:
        cmd = args[0]
        cmd = '$%s=`%s`' % (assign_to, cmd)
        args = list(args)
        args[0] = cmd
        
    if dryrun:
        cmd = args[0]
        print('[%s@localhost] local: %s' % (getpass.getuser(), cmd))
    else:
        return local(*args, **kwargs)
        
def run_or_dryrun(*args, **kwargs):
    dryrun = get_dryrun(kwargs.get('dryrun'))
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
    if dryrun:
        cmd = args[0]
        if BURLAP_COMMAND_PREFIX:
            print('%s run: %s' % (render_command_prefix(), cmd))
        else:
            print(cmd)
    else:
        return _run(*args, **kwargs)

def sudo_or_dryrun(*args, **kwargs):
    dryrun = get_dryrun(kwargs.get('dryrun'))
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
    if dryrun:
        cmd = args[0]
        if BURLAP_COMMAND_PREFIX:
            print('%s sudo: %s' % (render_command_prefix(), cmd))
        else:
            print(cmd)
    else:
        return _sudo(*args, **kwargs)

def reboot_or_dryrun(*args, **kwargs):
    from fabric.operations import reboot
    dryrun = get_dryrun(kwargs.get('dryrun'))
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
    if dryrun:
        print('%s sudo: reboot' % (render_command_prefix(),))
    else:
        if env.is_local:
            if raw_input('reboot now? ').strip()[0].lower() != 'y':
                return
        reboot(*args, **kwargs)

def put_or_dryrun(**kwargs):
    dryrun = get_dryrun(kwargs.get('dryrun'))
    use_sudo = kwargs.get('use_sudo', False)
    real_remote_path = None
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
    if dryrun:
        local_path = kwargs['local_path']
        remote_path = kwargs.get('remote_path', None)
        
        if not remote_path:
            _, remote_path = tempfile.mkstemp()
            
        if not remote_path.startswith('/') and not remote_path.startswith('~'):
            remote_path = '/tmp/' + remote_path
        
        if use_sudo:
            real_remote_path = remote_path
            _, remote_path = tempfile.mkstemp()
        
        if real_remote_path is None:
            real_remote_path = remote_path
        
        if env.host_string in LOCALHOSTS:
            cmd = 'rsync --progress --verbose %s %s' % (local_path, remote_path)
            print('%s put: %s' % (render_command_prefix(), cmd))
            env.put_remote_path = local_path
        else:
            cmd = 'rsync --progress --verbose %s %s@%s:%s' % (local_path, env.user, env.host_string, remote_path)
            env.put_remote_path = remote_path
            print('%s put: %s' % (render_command_prefix(), cmd))
            
        if real_remote_path and use_sudo:
            sudo_or_dryrun('mv %s %s' % (remote_path, real_remote_path))
            env.put_remote_path = real_remote_path
            
        return [real_remote_path]
    else:
        return _put(**kwargs)

def get_or_dryrun(**kwargs):
    dryrun = get_dryrun(kwargs.get('dryrun'))
    use_sudo = kwargs.get('use_sudo', False)
    if 'dryrun' in kwargs:
        del kwargs['dryrun']
    if dryrun:
        local_path = kwargs['local_path']
        remote_path = kwargs.get('remote_path', None)
        if not local_path:
            local_path = tempfile.mktemp()
        if not local_path.startswith('/'):
            local_path = '/tmp/' + local_path
        cmd = ('sudo ' if use_sudo else '')+'rsync --progress --verbose %s@%s:%s %s' % (env.user, env.host_string, remote_path, local_path)
        print('[localhost] get: %s' % (cmd,))
        env.get_local_path = local_path
        
    else:
        return _get(**kwargs)

def _get(*args, **kwargs):
    ret = __get(*args, **kwargs)
    env.get_local_path = ret
    return ret

def pretty_bytes(bytes):
    """
    Scales a byte count to the largest scale with a small whole number
    that's easier to read.
    Returns a tuple of the format (scaled_float, unit_string).
    """
    if not bytes:
        return bytes, 'bytes'
    sign = bytes/float(bytes)
    bytes = abs(bytes)
    for x in ['bytes','KB','MB','GB','TB']:
        if bytes < 1024.0:
            #return "%3.1f %s" % (bytes, x)
            return sign*bytes, x
        bytes /= 1024.0

def get_component_settings(prefixes=[]):
    """
    Returns a subset of the env dictionary containing
    only those keys with the name prefix.
    """
    data = {}
    for name in prefixes:
        name = name.lower().strip()
        for k in env:
            if k.startswith('%s_' % name):
                data[k] = env[k]
    return data

def get_last_modified_timestamp(path):
    """
    Recursively finds the most recent timestamp in the given directory.
    """
    cmd = 'find '+path+' -type f -printf "%T@ %p\n" | sort -n | tail -1 | cut -f 1 -d " "'
         #'find '+path+' -type f -printf "%T@ %p\n" | sort -n | tail -1 | cut -d " " -f1
    ret = subprocess.check_output(cmd, shell=True)
    # Note, we round now to avoid rounding errors later on where some formatters
    # use different decimal contexts.
    try: 
        ret = round(float(ret), 2)
    except ValueError:
        return
    return ret

def check_settings_for_differences(old, new, as_bool=False, as_tri=False):
    """
    Returns a subset of the env dictionary keys that differ,
    either being added, deleted or changed between old and new.
    """
    
    assert not as_bool or not as_tri
    
    old = old or {}
    new = new or {}
    
    changes = set(k for k in set(new.iterkeys()).intersection(old.iterkeys()) if new[k] != old[k])
    if changes and as_bool:
        return True
    
    added_keys = set(new.iterkeys()).difference(old.iterkeys())
    if added_keys and as_bool:
        return True
    if not as_tri:
        changes.update(added_keys)
    
    deled_keys = set(old.iterkeys()).difference(new.iterkeys())
    if deled_keys and as_bool:
        return True
    if as_bool:
        return False
    if not as_tri:
        changes.update(deled_keys)
    
    if as_tri:
        return added_keys, changes, deled_keys
    
    return changes

def get_subpackages(module):
    dir = os.path.dirname(module.__file__)
    def is_package(d):
        d = os.path.join(dir, d)
        return os.path.isdir(d) and glob.glob(os.path.join(d, '__init__.py*'))
    return filter(is_package, os.listdir(dir))

def get_submodules(module):
    dir = os.path.dirname(module.__file__)
    def is_module(d):
        d = os.path.join(dir, d)
        return os.path.isfile(d) and glob.glob(os.path.join(d, '*.py*'))
    return filter(is_module, os.listdir(dir))

def iter_apps():
    sys.path.insert(0, os.getcwd())
    arch = importlib.import_module('arch')
    settings = importlib.import_module('arch.settings')
    INSTALLED_APPS = set(settings.INSTALLED_APPS)
    for sub_name in get_subpackages(arch):
        if sub_name in INSTALLED_APPS:
            yield sub_name

def get_app_package(name):
    sys.path.insert(0, os.getcwd())
    arch = importlib.import_module('arch')
    settings = importlib.import_module('arch.settings')
    INSTALLED_APPS = set(settings.INSTALLED_APPS)
    assert name in INSTALLED_APPS, 'Unknown or uninstalled app: %s' % (name,)
    return importlib.import_module('arch.%s' % name)

def to_dict(obj):
    if isinstance(obj, (tuple, list)):
        return [to_dict(_) for _ in obj]
    elif isinstance(obj, dict):
        return dict((to_dict(k), to_dict(v)) for k,v in six.iteritems(obj))
    elif isinstance(obj, (int, bool, float, basestring)):
        return obj
    elif hasattr(obj, 'to_dict'):
        return obj.to_dict()
    else:
        raise Exception('Unknown type: %s %s' % (obj, type(obj)))

class QueuedCommand(object):
    """
    Represents a fabric command that is pending execution.
    """
    
    def __init__(self, name, args=None, kwargs=None, pre=[], post=[]):
        self.name = name
        self.args = args or []
        self.kwargs = kwargs or {}
        
        # Used for ordering commands.
        self.pre = pre # commands that should come before this command
        assert isinstance(self.pre, (tuple, list)), 'Pre must be a list type.'
        self.post = post # commands that should come after this command
        assert isinstance(self.post, (tuple, list)), 'Post must be a list type.'
    
    @property
    def cn(self):
        """
        Returns the component name, if given in the name
        as "<component_name>.method".
        """
        parts = self.name.split('.')
        if len(parts) >= 2:
            return parts[0]
    
    def __repr__(self):
        kwargs = list(map(str, self.args))
        for k,v in six.iteritems(self.kwargs):
            if isinstance(v, bool):
                kwargs.append('%s=%i' % (k,int(v)))
            elif isinstance(v, basestring) and '=' in v:
                # Escape equals sign character in parameter values.
                kwargs.append('%s="%s"' % (k, v.replace('=', '\=')))
            else:
                kwargs.append('%s=%s' % (k, v))
        params = (self.name, ','.join(kwargs))
        if params[1]:
            return ('%s:%s' % params).strip()
        else:
            return (params[0]).strip()
    
    def __cmp__(self, other):
        """
        Return negative if x<y, zero if x==y, positive if x>y.
        """
        if not isinstance(self, type(other)):
            return NotImplemented
        
        x_cn = self.cn
        x_name = self.name
        x_pre = self.pre
        x_post = self.post
        
        y_cn = other.cn
        y_name = other.name
        y_pre = other.pre
        y_post = other.post
        
        if y_cn in x_pre or y_name in x_pre:
            # Other should come first.
            return +1
        elif y_cn in x_post or y_name in x_post:
            # Other should come last.
            return -1
        elif x_cn in y_pre or x_name in y_pre:
            # We should come first.
            return -1
        elif x_cn in y_post or x_name in y_post:
            # We should come last.
            return -1
        return 0
        #return cmp(hash(self), hash(other))
    
    def __hash__(self):
        return hash((self.name, tuple(self.args), tuple(self.kwargs.items())))
    
    def __call__(self):
        raise NotImplementedError

def get_template_dirs():
    
    paths = (
        (env.ROLES_DIR, env[ROLE], 'templates'),
        (env.ROLES_DIR, env[ROLE]),
        (env.ROLES_DIR, '..', 'templates', env[ROLE]),
        (env.ROLES_DIR, '..', 'satchels', 'templates'),
        (env.ROLES_DIR, ALL, 'templates'),
        (env.ROLES_DIR, ALL),
        (env.ROLES_DIR, '..', 'templates', ALL),
        (env.ROLES_DIR, '..', 'templates'),
        (os.path.dirname(__file__), 'templates'),
    )
    
    for path in paths:
        if None in path:
            continue
        yield os.path.join(*path)
    env.template_dirs = get_template_dirs()

env.template_dirs = get_template_dirs()

def save_env():
    env_default = {}
    for k, v in six.iteritems(env):
        if k.startswith('_'):
            continue
        elif isinstance(v, (types.GeneratorType, types.ModuleType)):
            continue
        env_default[k] = copy.deepcopy(v)
    return env_default

try:
    from django.conf import settings as _settings
    _settings.configure(TEMPLATE_DIRS=env.template_dirs)
except ImportError:
    warnings.warn('Unable to import Django settings.', ImportWarning)

def _put(**kwargs):
    local_path = kwargs['local_path']
    fd, fn = tempfile.mkstemp()
    if not env.is_local:
        os.remove(fn)
    #kwargs['remote_path'] = kwargs.get('remote_path', '/tmp/%s' % os.path.split(local_path)[-1])
    kwargs['remote_path'] = kwargs.get('remote_path', fn)
    env.put_remote_path = kwargs['remote_path']
    return __put(**kwargs)

def get_rc(k):
    return env._rc.get(env[ROLE], type(env)()).get(k)

def set_rc(k, v):
    env._rc.setdefault(env[ROLE], type(env)())
    env._rc[env[ROLE]][k] = v

def get_packager():
    """
    Returns the packager detected on the remote system.
    """
    common_packager = get_rc('common_packager')
    if common_packager:
        return common_packager
    #TODO:cache result by current env.host_string so we can handle multiple hosts with different OSes
    with settings(warn_only=True) as a, hide('running', 'stdout', 'stderr', 'warnings') as b:
            ret = _run('cat /etc/fedora-release')
            if ret.succeeded:
                common_packager = YUM
            else:
                ret = _run('cat /etc/lsb-release')
                if ret.succeeded:
                    common_packager = APT
                else:
                    for pn in PACKAGERS:
                        ret = run_or_dryrun('which %s' % pn)
                        if ret.succeeded:
                            common_packager = pn
                            break
    if not common_packager:
        raise Exception('Unable to determine packager.')
    set_rc('common_packager', common_packager)
    return common_packager

def get_os_version():
    """
    Returns a named tuple describing the operating system on the remote host.
    """
    common_os_version = get_rc('common_os_version')
    if common_os_version:
        return common_os_version
    with settings(warn_only=True), hide('running', 'stdout', 'stderr', 'warnings'):
        
        ret = _run('cat /etc/lsb-release')
        if ret.succeeded:
            return OS(
                type = LINUX,
                distro = UBUNTU,
                release = re.findall('DISTRIB_RELEASE=([0-9\.]+)', ret)[0])
                
        ret = _run('cat /etc/debian_version')
        if ret.succeeded:
            return OS(
                type = LINUX,
                distro = DEBIAN,
                release = re.findall('([0-9\.]+)', ret)[0])
    
        ret = _run('cat /etc/fedora-release')
        if ret.succeeded:
            return OS(
                type = LINUX,
                distro = FEDORA,
                release = re.findall('release ([0-9]+)', ret)[0])

        raise Exception('Unable to determine OS version.')

def find_template(template):
    verbose = get_verbose()
    final_fqfn = None
    for path in get_template_dirs():
        if verbose:
            print('Checking: %s' % path)
        fqfn = os.path.abspath(os.path.join(path, template))
        if os.path.isfile(fqfn):
            if verbose:
                print('Using template: %s' % (fqfn,))
            final_fqfn = fqfn
            break
        else:
            if verbose:
                print('Template not found: %s' % (fqfn,))
    return final_fqfn

def get_template_contents(template):
    final_fqfn = find_template(template)
    return open(final_fqfn).read()

def render_to_string(template, extra=None):
    """
    Renders the given template to a string.
    """
    #import django
    #from django.template import Context, Template
    #from django.template.loader import render_to_string
    from jinja2 import Template
    
    extra = extra or {}
    
    final_fqfn = find_template(template)
    assert final_fqfn, 'Template not found: %s' % template
    #from django.conf import settings
#     try:
#         settings.configure()
#     except RuntimeError:
#         pass
    
    #content = render_to_string('template.txt', dict(env=env))
    template_content = open(final_fqfn, 'r').read()
    t = Template(template_content)
    #c = Context(env)
    if extra:
        context = env.copy()
        context.update(extra)
    else:
        context = env
    rendered_content = t.render(**context)
    rendered_content = rendered_content.replace('&quot;', '"')
    return rendered_content

def render_to_file(template, fn=None, extra=None, **kwargs):
    """
    Returns a template to a file.
    If no filename given, a temporary filename will be generated and returned.
    """
    import tempfile
    dryrun = get_dryrun(kwargs.get('dryrun'))
#     replace_homedir = kwargs.pop('replace_homedir', False)
    append_newline = kwargs.pop('append_newline', True)
    content = render_to_string(template, extra=extra)
    if append_newline and not content.endswith('\n'):
        content += '\n'
#     if env.user:
#         content = content.replace('~', '/home/%s' % env.user)
    if dryrun:
        if not fn:
            fd, fn = tempfile.mkstemp()
            fout = os.fdopen(fd, 'wt')
            fout.close()
    else:
        if fn:
            fout = open(fn, 'w')
        else:
            fd, fn = tempfile.mkstemp()
            fout = os.fdopen(fd, 'wt')
        fout.write(content)
        fout.close()
    assert fn
    
    cmd = 'echo -e %s > %s' % (shellquote(content), fn)
    if BURLAP_COMMAND_PREFIX:
        print('%s run: %s' % (render_command_prefix(), cmd))
    else:
        print(cmd)

    return fn

def install_script(local_path=None, remote_path=None, render=True, extra=None):
    local_path = find_template(local_path)
    if render:
        extra = extra or {}
        local_path = render_to_file(template=local_path, extra=extra)
    put_or_dryrun(local_path=local_path, remote_path=remote_path, use_sudo=True)
    sudo_or_dryrun('chmod +x %s' % env.put_remote_path)
    
def write_to_file(content, fn=None):
    import tempfile
    if fn:
        fout = open(fn, 'w')
    else:
        fd, fn = tempfile.mkstemp()
        fout = os.fdopen(fd, 'wt')
    fout.write(content)
    fout.close()
    return fn

def set_site(site):
    if site is None:
        return
    env[SITE] = os.environ[SITE] = site

def iter_sites(sites=None, site=None, renderer=None, setter=None, no_secure=False, verbose=False):
    """
    Iterates over sites, safely setting environment variables for each site.
    """
    from dj import render_remote_paths
    if sites is None:
        site = site or env.SITE
        if site == ALL:
            sites = six.iteritems(env.sites)
        else:
            sites = [(site, env.sites[site])]
        
    renderer = renderer or render_remote_paths
    env_default = save_env()
    for site, site_data in sites:
        if no_secure and site.endswith('_secure'):
            continue
        env.update(env_default)
        env.update(env.sites[site])
        env.SITE = site
        renderer()
        if setter:
            setter(site)
        yield site, site_data
    env.update(env_default)

def pc(*args):
    """
    Print comment.
    """
    print('echo "%s"' % ' '.join(map(str, args)))

def get_current_hostname():
    key = '_ip_to_hostname'
    if key not in env:
        env[key] = {}
#    import importlib
#    
#    retriever = None
#    if env.hosts_retriever:
#        # Dynamically retrieve hosts.
#        module_name = '.'.join(env.hosts_retriever.split('.')[:-1])
#        func_name = env.hosts_retriever.split('.')[-1]
#        retriever = getattr(importlib.import_module(module_name), func_name)
#    
#    # Load host translator.
#    translator = None
#    if hostname:
#        # Filter hosts list by a specific host name.
#        module_name = '.'.join(env.hostname_translator.split('.')[:-1])
#        func_name = env.hostname_translator.split('.')[-1]
#        translator = getattr(importlib.import_module(module_name), func_name)
    #ret = run_or_dryrun('hostname')#)
    
    if env.host_string not in env[key]:
        ret = _run('hostname')
        env[key][env.host_string] = str(ret).strip()
        
    return env[key][env.host_string]

#http://stackoverflow.com/questions/11557241/python-sorting-a-dependency-list
def topological_sort(source):
    """perform topo sort on elements.

    :arg source: list of ``(name, [list of dependancies])`` pairs
    :returns: list of names, with dependancies listed first
    """
    if isinstance(source, dict):
        source = source.items()
    pending = sorted([(name, set(deps)) for name, deps in source]) # copy deps so we can modify set in-place       
    emitted = []        
    while pending:
        next_pending = []
        next_emitted = []
        for entry in pending:
            name, deps = entry
            deps.difference_update(emitted) # remove deps we emitted last pass
            if deps: # still has deps? recheck during next pass
                next_pending.append(entry) 
            else: # no more deps? time to emit
                yield name 
                emitted.append(name) # <-- not required, but helps preserve original ordering
                next_emitted.append(name) # remember what we emitted for difference_update() in next pass
        if not next_emitted: # all entries have unmet deps, one of two things is wrong...
            raise ValueError("cyclic or missing dependancy detected: %r" % (next_pending,))
        pending = next_pending
        emitted = next_emitted

def represent_ordereddict(dumper, data):
    value = []

    for item_key, item_value in data.items():
        node_key = dumper.represent_data(item_key)
        node_value = dumper.represent_data(item_value)

        value.append((node_key, node_value))

    return yaml.nodes.MappingNode(u'tag:yaml.org,2002:map', value)

yaml.add_representer(OrderedDict, represent_ordereddict)

#TODO:make thread/process safe with lockfile?

shelf = Shelf()

def get_host_ip(hostname):
    #TODO:use generic host retriever?
    from burlap.vm import list_instances
    data = list_instances(show=0, verbose=0)
    for key, attrs in six.iteritems(data):
#         print('key:',key,attrs)
        if key == hostname:
            return attrs.get('ip')

def only_hostname(s):
    """
    Given an SSH target, returns only the hostname.
    
    e.g. only_hostname('user@mydomain:port') == 'mydomain'
    """
    return s.split('@')[-1].split(':')[0].strip()

def get_hosts_for_site(site=None):
    """
    Returns a list of hosts that have been configured to support the given site.
    """
    site = site or env.SITE
    hosts = set()
    for hostname, _sites in six.iteritems(env.available_sites_by_host):
#         print('checking hostname:',hostname, _sites)
        for _site in _sites:
            if _site == site:
#                 print( '_site:',_site)
                host_ip = get_host_ip(hostname)
#                 print( 'host_ip:',host_ip)
                if host_ip:
                    hosts.add(host_ip)
                    break
    return list(hosts)

def getoutput(cmd):
    return subprocess.check_output(cmd, shell=True)
#     process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
#     out, err = process.communicate()
#     return out
    
