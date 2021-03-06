"""
Supervisor processes
====================

This module provides high-level tools for managing long-running
processes using `supervisor`_.

.. _supervisor: http://supervisord.org/

"""

from burlap.files import watch
from burlap.supervisor import update_config, process_status, start_process
from burlap.system import UnsupportedFamily, distrib_family, distrib_id

from fabric.api import hide, settings

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


def process(name, **kwargs):
    """
    Require a supervisor process to be running.

    Keyword arguments will be used to build the program configuration
    file. Some useful arguments are:

    - ``command``: complete command including arguments (**required**)
    - ``directory``: absolute path to the working directory
    - ``user``: run the process as this user
    - ``stdout_logfile``: absolute path to the log file

    You should refer to the `supervisor documentation`_ for the
    complete list of allowed arguments.

    .. note:: the default values for the following arguments differs from
              the ``supervisor`` defaults:

              - ``autorestart``: defaults to ``true``
              - ``redirect_stderr``: defaults to ``true``

    Example::

        from burlap import require

        require.supervisor.process('myapp',
            command='/path/to/venv/bin/myapp --config production.ini --someflag',
            directory='/path/to/working/dir',
            user='alice',
            stdout_logfile='/path/to/logs/myapp.log',
            )

    .. _supervisor documentation: http://supervisord.org/configuration.html#program-x-section-values
    """

    from burlap.require import file as require_file
    from burlap.require.deb import package as require_deb_package
    from burlap.require.rpm import package as require_rpm_package
    from burlap.require.arch import package as require_arch_package
    from burlap.require.service import started as require_started

    family = distrib_family()

    if family == 'debian':
        require_deb_package('supervisor')
        require_started('supervisor')
    elif family == 'redhat':
        require_rpm_package('supervisord')
        require_started('supervisord')
    elif family == 'arch':
        require_arch_package('supervisor')
        require_started('supervisord')
    else:
        raise UnsupportedFamily(supported=['debian', 'redhat', 'arch'])

    # Set default parameters
    params = {}
    params.update(kwargs)
    params.setdefault('autorestart', 'true')
    params.setdefault('redirect_stderr', 'true')

    # Build config file from parameters
    lines = []
    lines.append('[program:%(name)s]' % locals())
    for key, value in sorted(params.items()):
        lines.append("%s=%s" % (key, value))

    # Upload config file
    if family == 'debian':
        filename = '/etc/supervisor/conf.d/%(name)s.conf' % locals()
    elif family == 'redhat':
        filename = '/etc/supervisord.d/%(name)s.ini' % locals()
    elif family == 'arch':
        filename = '/etc/supervisor.d/%(name)s.ini' % locals()

    with watch(filename, callback=update_config, use_sudo=True):
        require_file(filename, contents='\n'.join(lines), use_sudo=True)

    # Start the process if needed
    if process_status(name) == 'STOPPED':
        start_process(name)
