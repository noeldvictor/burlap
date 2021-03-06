"""
Supervisor processes
====================

This module provides high-level tools for managing long-running
processes using `supervisord`_.

.. _supervisord: http://supervisord.org/

"""
from __future__ import print_function

import os
import re
import time

from fabric.api import hide, settings

from burlap.constants import *
from burlap import ServiceSatchel
from burlap.utils import run_as_root


def reload_config():
    """
    Reload supervisor configuration.
    """
    run_as_root("supervisorctl reload")


def update_config():
    """
    Reread and update supervisor job configurations.

    Less heavy-handed than a full reload, as it doesn't restart the
    backend supervisor process and all managed processes.
    """
    run_as_root("supervisorctl update")


def process_status(name):
    """
    Get the status of a supervisor process.
    """
    with settings(hide('running', 'stdout', 'stderr', 'warnings'), warn_only=True):
        res = run_as_root("supervisorctl status %(name)s" % locals())
        if res.startswith("No such process"):
            return None
        else:
            return res.split()[1]


def start_process(name):
    """
    Start a supervisor process
    """
    run_as_root("supervisorctl start %(name)s" % locals())


def stop_process(name):
    """
    Stop a supervisor process
    """
    run_as_root("supervisorctl stop %(name)s" % locals())


def restart_process(name):
    """
    Restart a supervisor process
    """
    run_as_root("supervisorctl restart %(name)s" % locals())


class SupervisorSatchel(ServiceSatchel):
    
    name = 'supervisor'
    
    ## Service options.
    
    #ignore_errors = True
    
    post_deploy_command = 'restart'
    
    tasks = (
        'configure',
    )
    
    required_system_packages = {
        UBUNTU: ['supervisor'],
    }
    
    def set_defaults(self):
    
        self.env.config_template = 'supervisor/supervisor_daemon.template2.config'
        self.env.config_path = '/etc/supervisor/supervisord.conf'
        #/etc/supervisor/conf.d/celery_
        self.env.conf_dir = '/etc/supervisor/conf.d'
        self.env.daemon_bin_path_template = '%(pip_virtual_env_dir)s/bin/supervisord'
        self.env.daemon_path = '/etc/init.d/supervisord'
        self.env.bin_path_template = '%(pip_virtual_env_dir)s/bin'
        self.env.daemon_pid = '/var/run/supervisord.pid'
        self.env.log_path = "/var/log/supervisord.log"
        self.env.supervisorctl_path_template = '%(pip_virtual_env_dir)s/bin/supervisorctl'
        self.env.kill_pattern = ''
        self.env.max_restart_wait_minutes = 5
        
        self.env.services = []
        
        # Functions that, when called, should return a supervisor service text
        # ready to be appended to supervisord.conf.
        # It will be called once for each site.
        self.genv._supervisor_create_service_callbacks = []
        
        self.env.service_commands = {
            START:{
                FEDORA: 'systemctl start supervisord.service',
                UBUNTU: 'service supervisor start',
            },
            STOP:{
                FEDORA: 'systemctl stop supervisor.service',
                UBUNTU: 'service supervisor stop',
            },
            DISABLE:{
                FEDORA: 'systemctl disable httpd.service',
                UBUNTU: 'chkconfig supervisor off',
            },
            ENABLE:{
                FEDORA: 'systemctl enable httpd.service',
                UBUNTU: 'chkconfig supervisor on',
            },
            RESTART:{
                FEDORA: 'systemctl restart supervisord.service',
                UBUNTU: 'service supervisor restart; sleep 5',
            },
            STATUS:{
                FEDORA: 'systemctl status supervisord.service',
                UBUNTU: 'service supervisor status',
            },
        }
    
    def render_paths(self):
        from pip import render_paths as pip_render_paths
        pip_render_paths()
        self.genv.supervisor_daemon_bin_path = self.genv.supervisor_daemon_bin_path_template % self.genv
        self.genv.supervisor_bin_path = self.genv.supervisor_bin_path_template % self.genv
        self.genv.supervisor_supervisorctl_path = self.genv.supervisor_supervisorctl_path_template % self.genv
    
    def register_callback(self, f):
        self.genv._supervisor_create_service_callbacks.append(f)
        
    def restart(self):
        """
        Supervisor can take a very long time to start and stop,
        so wait for it.
        """
        n = 60
        sleep_n = int(self.env.max_restart_wait_minutes/10.*60)
        for _ in xrange(n):
            self.stop()
            if self.dryrun or not self.is_running():
                break
            print('Waiting for supervisor to stop (%i of %i)...' % (_, n))
            time.sleep(sleep_n)
        self.start()
        for _ in xrange(n):
            if self.dryrun or self.is_running():
                return
            print('Waiting for supervisor to start (%i of %i)...' % (_, n))
            time.sleep(sleep_n)
        raise Exception('Failed to restart service %s!' % self.name)
    
    def record_manifest(self):
        """
        Called after a deployment to record any data necessary to detect changes
        for a future deployment.
        """
        from burlap.common import get_component_settings
        
        data = get_component_settings(self.name)
        
        # Celery deploys itself through supervisor, so monitor its changes too in Apache site configs.
        for site_name, site_data in self.genv.sites.iteritems():
            if self.verbose:
                print(site_name, site_data)
            data['celery_has_worker_%s' % site_name] = site_data.get('celery_has_worker', False)
        
        data['configured'] = True
        
        return data

    def deploy_services(self, site=None):
        """
        Collects the configurations for all registered services and writes
        the appropriate supervisord.conf file.
        """
        from burlap.common import iter_sites
        
        verbose = self.verbose
        
        self.render_paths()
        
        supervisor_services = []
        process_groups = []
        
        for site, site_data in iter_sites(site=site, renderer=self.render_paths):
            if verbose:
                print(site)
            for cb in self.genv._supervisor_create_service_callbacks:
                ret = cb()
                if isinstance(ret, basestring):
                    supervisor_services.append(ret)
                elif isinstance(ret, tuple):
                    assert len(ret) == 2
                    conf_name, conf_content = ret
                    if verbose:
                        print('conf_name:', conf_name)
                        print('conf_content:', conf_content)
                    remote_fn = os.path.join(self.env.conf_dir, conf_name)
                    local_fn = self.write_to_file(conf_content)
                    self.put_or_dryrun(local_path=local_fn, remote_path=remote_fn, use_sudo=True)
                    
                    process_groups.append(os.path.splitext(conf_name)[0])
                    
        self.env.services_rendered = '\n'.join(supervisor_services)
    
        fn = self.render_to_file(self.env.config_template)
        self.put_or_dryrun(local_path=fn, remote_path=self.env.config_path, use_sudo=True)
        
        for pg in process_groups:
            self.sudo_or_dryrun('supervisorctl add %s' % pg)
        
        #TODO:are all these really necessary?
        self.sudo_or_dryrun('supervisorctl restart all')
        self.sudo_or_dryrun('supervisorctl reread')
        self.sudo_or_dryrun('supervisorctl update')
    
    def configure(self, **kwargs):
        kwargs['site'] = ALL
        
#         last_manifest = supervisor_satchel.last_manifest
#         if not last_manifest or not last_manifest.get('configured'):
#             configure()
        
        self.deploy_services(**kwargs)
        
    
    configure.deploy_before = ['packager', 'user', 'rabbitmq']
        
supervisor_satchel = SupervisorSatchel()
