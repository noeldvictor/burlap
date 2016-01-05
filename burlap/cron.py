import os
import sys

from burlap import ServiceSatchel
from burlap.constants import * 

class CronSatchel(ServiceSatchel):
    
    name = 'cron'
    
    ## Service options.
    
    #ignore_errors = True
    
    tasks = (
        'configure',
    )
    
    post_deploy_command = None
    
    required_system_packages = {
        FEDORA: ['crontabs'],
        (UBUNTU, '12.04'): ['cron'],
    }

    def set_defaults(self):
        self.env.enabled = True
        self.env.crontabs_available = type(self.genv)() # {name:[cron lines]}
        self.env.command = 'cron'
        self.env.user = 'www-data'
        self.env.python = None
        self.env.crontab_headers = ['PATH=/usr/sbin:/usr/bin:/sbin:/bin\nSHELL=/bin/bash']
        self.env.django_manage_template = '%(remote_app_src_package_dir)s/manage.py'
        self.env.stdout_log_template = '/tmp/chroniker-%(SITE)s-stdout.$(date +\%%d).log'
        self.env.stderr_log_template = '/tmp/chroniker-%(SITE)s-stderr.$(date +\%%d).log'
        self.env.crontabs_selected = [] # [name]
           
        self.env.service_commands = {
            START:{
                FEDORA: 'systemctl start crond.service',
                UBUNTU: 'service cron start',
            },
            STOP:{
                FEDORA: 'systemctl stop crond.service',
                UBUNTU: 'service cron stop',
            },
            DISABLE:{
                FEDORA: 'systemctl disable crond.service',
                UBUNTU: 'chkconfig cron off',
                (UBUNTU, '14.04'): 'update-rc.d -f cron remove',
            },
            ENABLE:{
                FEDORA: 'systemctl enable crond.service',
                UBUNTU: 'chkconfig cron on',
                (UBUNTU, '14.04'): 'update-rc.d cron defaults',
            },
            RESTART:{
                FEDORA: 'systemctl restart crond.service',
                UBUNTU: 'service cron restart; sleep 3',
            },
            STATUS:{
                FEDORA: 'systemctl status crond.service',
                UBUNTU: 'service cron status',
            },
        }
        
    def render_paths(self):
        from pip import render_paths as pip_render_paths
        
        pip_render_paths()
        
        self.env.python = os.path.join(self.genv.pip_virtual_env_dir, 'bin', 'python')
        self.env.django_manage = self.env.django_manage_template % self.genv
        self.env.stdout_log = self.env.stdout_log_template % self.genv
        self.env.stderr_log = self.env.stderr_log_template % self.genv
    
    def deploy(self, site=None, verbose=0):
        """
        Writes entire crontab to the host.
        """
        from burlap.common import get_current_hostname, iter_sites
        
        verbose = int(verbose)
        cron_crontabs = []
        hostname = get_current_hostname()
        target_sites = self.genv.available_sites_by_host.get(hostname, None)
        if verbose:
            print>>sys.stderr, 'hostname: "%s"' % (hostname,) 
        for site, site_data in iter_sites(site=site, renderer=self.render_paths):
            if verbose:
                print>>sys.stderr, 'site:',site
            #print 'cron_crontabs_selected:',env.crontabs_selected
            
            # Only load site configurations that are allowed for this host.
            if target_sites is None:
                pass
            else:
                assert isinstance(target_sites, (tuple, list))
                if site not in target_sites:
                    print>>sys.stderr, 'Skipping:', site
                    continue
            
            if verbose:
                print>>sys.stderr, 'env.crontabs_selected:', self.env.crontabs_selected
            for selected_crontab in self.env.crontabs_selected:
                lines = self.env.crontabs_available.get(selected_crontab, [])
                if verbose:
                    print>>sys.stderr, 'lines:',lines
                for line in lines:
                    cron_crontabs.append(line % env)
        
        if not cron_crontabs:
            return
        
        cron_crontabs = self.env.crontab_headers + cron_crontabs
        cron_crontabs.append('\n')
        self.env.crontabs_rendered = '\n'.join(cron_crontabs)
        fn = self.write_to_file(content=env.crontabs_rendered)
        if self.dryrun:
            print 'echo %s > %s' % (env.crontabs_rendered, fn)
        self.put_or_dryrun(local_path=fn)
        self.sudo_or_dryrun('crontab -u %(cron_user)s %(put_remote_path)s' % env)
    
    def configure(self, **kwargs):
        if self.env.enabled:
            kwargs['site'] = ALL
            self.deploy(**kwargs)
            self.enable()
            self.restart()
        else:
            self.disable()
            self.stop()
    configure.is_deployer = True
    configure.deploy_before = ['packager', 'user', 'tarball']
        
CronSatchel()
