"""
Convenience decorators for use in fabfiles.
"""
from __future__ import with_statement

import types
from functools import wraps
import inspect

#from Crypto import Random

#from fabric import tasks
#from .context_managers import settings
from burlap.tasks import WrappedCallableTask

def task_or_dryrun(*args, **kwargs):
    """
    Decorator declaring the wrapped function to be a new-style task.

    May be invoked as a simple, argument-less decorator (i.e. ``@task``) or
    with arguments customizing its behavior (e.g. ``@task(alias='myalias')``).

    Please see the :ref:`new-style task <task-decorator>` documentation for
    details on how to use this decorator.

    .. versionchanged:: 1.2
        Added the ``alias``, ``aliases``, ``task_class`` and ``default``
        keyword arguments. See :ref:`task-decorator-arguments` for details.
    .. versionchanged:: 1.5
        Added the ``name`` keyword argument.

    .. seealso:: `~fabric.docs.unwrap_tasks`, `~fabric.tasks.WrappedCallableTask`
    """
    invoked = bool(not args or kwargs)
    task_class = kwargs.pop("task_class", WrappedCallableTask)
#     if invoked:
#         func, args = args[0], ()
#     else:
    func, args = args[0], ()

    def wrapper(func):
        return task_class(func, *args, **kwargs)
    wrapper.is_task_or_dryrun = True
    wrapper.wrapped = func

    return wrapper if invoked else wrapper(func)

def task(meth):
    meth.is_task = True
    return meth
