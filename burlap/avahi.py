from __future__ import print_function

from burlap import ServiceSatchel
from burlap.constants import *

class AvahiSatchel(ServiceSatchel):
    
    name = 'avahi'
    
    ## Service options.
    
    #ignore_errors = True
    
    required_system_packages = {
        UBUNTU: ['avahi-daemon'],
        DEBIAN: ['avahi-daemon'],
    }

    def set_defaults(self):
            
        self.env.daemon_name = 'avahi-daemon'
    
        self.env.service_commands = {
            START:{
                UBUNTU: 'service %s start' % self.env.daemon_name,
                DEBIAN: 'service %s start' % self.env.daemon_name,
            },
            STOP:{
                UBUNTU: 'service %s stop' % self.env.daemon_name,
                DEBIAN: 'service %s stop' % self.env.daemon_name,
            },
            DISABLE:{
                UBUNTU: 'chkconfig %s off' % self.env.daemon_name,
                DEBIAN: 'update-rc.d %s disable' % self.env.daemon_name,
            },
            ENABLE:{
                UBUNTU: 'chkconfig %s on' % self.env.daemon_name,
                DEBIAN: 'update-rc.d %s enable' % self.env.daemon_name,
            },
            RESTART:{
                UBUNTU: 'service %s restart' % self.env.daemon_name,
                DEBIAN: 'service %s restart' % self.env.daemon_name,
            },
            STATUS:{
                UBUNTU: 'service %s status' % self.env.daemon_name,
                DEBIAN: 'service %s status' % self.env.daemon_name,
            },
        }
    
    def configure(self):
        if self.env.enabled:
            self.install_packages()
            self.enable()
            self.restart()
        else:
            self.disable()
            self.stop()
        #sudo_or_dryrun('apt-get install avahi-daemon')
        
        #TODO:
        #sudo_or_dryrun('nano /etc/avahi/avahi-daemon.conf')
        
        #sudo_or_dryrun('service avahi-daemon restart')
        #sudo_or_dryrun('update-rc.d avahi-daemon defaults')
        
    
    configure.deploy_before = ['packager', 'user']
    
AvahiSatchel()
