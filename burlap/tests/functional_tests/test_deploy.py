from __future__ import print_function

import os, sys, shutil

import yaml

from fabric.api import puts, sudo, env

from burlap.shelf import Shelf
from burlap.debug import shell
from burlap.context import set_cwd
from burlap.common import getoutput
from burlap.deploy import run as deploy_run, preview as deploy_preview
from burlap import load_role_handler

def test_deploy():
    """
    Creates a multi-site Apache Django powered web server with a MySQL backend.
    """
    
    try:
        
        # Delete any old tmp files
        PROJECT_DIR = '/tmp/burlap_test'
        if os.path.exists(PROJECT_DIR):
            shutil.rmtree(PROJECT_DIR)
        os.makedirs(PROJECT_DIR)
     
        # Create our test virtualenv.
        PYTHON_EXE = os.path.split(sys.executable)[-1]
        VIRTUALENV_DIR = os.path.join(PROJECT_DIR, '.env')
        BURLAP_DIR = os.path.abspath('./burlap')
        BURLAP_BIN = os.path.abspath('./bin/burlap')
        SITE_PACKAGES = os.path.join(VIRTUALENV_DIR, 'lib/%s/site-packages' % PYTHON_EXE)
     
        # Initialize project.
        kwargs = dict(
            project_dir=PROJECT_DIR,
            burlap_bin=BURLAP_BIN,
        )
        assert not os.system('cd {project_dir}; {burlap_bin} skel multitenant'.format(**kwargs))
     
        # Symlink burlap.
        assert not os.system('ln -s %s %s' % (BURLAP_DIR, SITE_PACKAGES))
        
        # Add production role.
        VIRTUALENV_ACTIVATE = '. %s/bin/activate' % VIRTUALENV_DIR
        kwargs = dict(
            project_dir=PROJECT_DIR,
            activate=VIRTUALENV_ACTIVATE,
            burlap_bin=BURLAP_BIN,
        )
        assert not os.system('cd {project_dir}; {burlap_bin} add-role prod'.format(**kwargs))
         
        # Test logging in to VM.
        print('env.host_string:', env.host_string, env.host)
        print('env.user:', env.user)
        print('env.key_filename:', env.key_filename)
        env.ROLE = 'prod'
        prod_settings = Shelf(filename=PROJECT_DIR+'/roles/{role}/settings.yaml')
        prod_settings['hosts'] = [env.host] if isinstance(env.host, basestring) else env.host
        #prod_settings['host_string']
        prod_settings['user'] = env.user
        prod_settings['key_filename'] = env.key_filename
        prod_settings['is_local'] = False
        prod_settings['app_name'] = 'multitenant'
        kwargs = dict(
            project_dir=PROJECT_DIR,
        )
        assert not os.system('cd {project_dir}; . ./shell; fab prod shell:command="echo hello"'.format(**kwargs))
        out = getoutput('cd {project_dir}; . ./shell; fab prod shell:command="ifconfig"'.format(**kwargs))
        print('out:', out)
        assert 'inet addr:10.0.2.15' in out
        
        # Add services.
        services = prod_settings.get('services', [])
        services.extend([
            'apache',
            #'hostname',
            'mysql',
            'mysqlclient',
            'ntpclient',
            'packager',
            'pip',
            'sshnice',
            'tarball',
            'timezone',
            'ubuntumultiverse',
            'unattendedupgrades',
            #'user',
        ])
        prod_settings.set('services', services)
        prod_settings.set('sites', {
            'multitenant': {
                'apache_domain_template': 'multitenant.test.com',
                'apache_domain_with_sub_template': 'multitenant.test.com',
                'apache_domain_without_sub_template': 'multitenant.test.com',
                'apache_server_aliases_template': 'multitenant.test.com',
                'apache_ssl': False,
                'apache_auth_basic': False,
                'apache_enforce_subdomain': False,
            }
        })
        
        # Confirm deployment changes are detected.
        with set_cwd(PROJECT_DIR):
            #from burlap import role_prod as prod
            prod = load_role_handler('prod')
    #         print('A', prod)
            prod()
            assert 'app_name' in env
            assert 'sites' in env
            env.host_string = env.hosts[0]
    #         print('B')
    #         print('env.host_string:', env.host_string, env.host)
    #         print('env.user:', env.user)
    #         print('env.key_filename:', env.key_filename)
            
            changed_components, deploy_funcs = deploy_preview()
            print('changed_components:', changed_components)
            assert changed_components == [
                'MYSQLCLIENT',
                'UBUNTUMULTIVERSE',
                'PACKAGER',
                'PIP',
                'SSHNICE',
                'TIMEZONE',
                'UNATTENDEDUPGRADES',
                'APACHE',
                'MYSQL',
                'NTPCLIENT',
            ]
            print('deploy_funcs:', deploy_funcs)
            assert deploy_funcs == [
                ('mysqlclient.configure', None),
                ('ubuntumultiverse.configure', None),
                ('packager.configure', None),
                ('pip.configure', None),
                ('sshnice.configure', None),
                ('timezone.configure', None),
                ('unattendedupgrades.configure', None),
                ('apache.configure', None),
                ('mysql.configure', None),
                ('ntpclient.configure', None),
            ]
        
        # Deploy changes.
        
        # Confirm changes have been cleared.
        
        # Add Django site.
        
    finally:
        # Undo changes to the VM.
        pass
        