"""
Helper functions for sending a notification email after each deployment.
"""
from __future__ import print_function

from burlap.constants import *
from burlap import Satchel

class DeploymentNotifierSatchel(Satchel):
    
    name = 'deploymentnotifier'
    
    tasks = (
        'notify_post_deployment',
    )
    
    def set_defaults(self):
    
        self.env.email_enabled = False
        self.env.email_host = None
        self.env.email_port = 587
        self.env.email_host_user = None
        self.env.email_host_password = None
        self.env.email_use_tls = True
        self.env.email_recipient_list = []

    def send_email(self, subject, message, from_email=None, recipient_list=[]):
        import smtplib
        from email.mime.text import MIMEText
        
        if not recipient_list:
            return
        
        from_email = from_email or self.env.email_host_user
        
        msg = MIMEText(message)
        
        # me == the sender's email address
        # you == the recipient's email address
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = '; '.join(recipient_list)
        
        # Send the message via our own SMTP server, but don't include the
        # envelope header.
        if self.verbose:
            print('Attempting to send mail using %s...' % self.env.email_host)
        s = smtplib.SMTP(self.env.email_host, self.env.email_port)
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(self.env.email_host_user, self.env.email_host_password)
        s.sendmail(from_email, recipient_list, msg.as_string())
        s.quit()

    def notify_post_deployment(self, subject=None, message=None, force=0):
        from burlap import common
        force = int(force)
        subject = subject or '%s Deployment Complete' % self.genv.ROLE.title()
        message = message or 'Deployment to %s is complete.' % self.genv.ROLE
        if self.dryrun:
            self.print_command('echo -e "{body}" | mail -s "{subject}" {recipients}'.format(
                recipients=','.join(self.env.email_recipient_list),
                body=message.replace('\n', '\\n'),
                subject=subject,
            ))
        elif force or (self.env.email_enabled and self.genv.host_string == self.genv.hosts[-1]):
            self.send_email(
                subject=subject,
                message=message,
                recipient_list=self.env.email_recipient_list)
                
class LoginNotifierSatchel(Satchel):
    
    name = 'loginnotifier'
    
    tasks = (
        'configure',
    )
    
    def set_defaults(self):
        self.env.sysadmin_email = 'root@localhost'
        self.env.script_template = 'notifier/loginnotifier.template.sh'
        self.env.script_installation_path = '/etc/profile.d/loginnotifier.sh'
        self.env.script_user = 'root'
        self.env.script_group = 'root'
        self.env.script_chmod = 'u+rx,g+rx'
        
    def configure(self):
        
        if self.env.enabled:
            fn = self.render_to_file(self.env.script_template)
            self.put_or_dryrun(
                local_path=fn,
                remote_path=self.env.script_installation_path, use_sudo=True)
                
            cmd = 'chown {script_user}:{script_group} {script_installation_path}'.format(**self.lenv)
            self.sudo_or_dryrun(cmd)
            
            cmd = 'chmod {script_chmod} {script_installation_path}'.format(**self.lenv)
            self.sudo_or_dryrun(cmd)
            
        else:
            self.sudo_or_dryrun('rm {script_installation_path}')
            
    
    configure.deploy_before = ['packager', 'user']

deployment_notifier = DeploymentNotifierSatchel()
notify_post_deployment = deployment_notifier.notify_post_deployment
send_email = deployment_notifier.send_email

login_notifier = LoginNotifierSatchel()
