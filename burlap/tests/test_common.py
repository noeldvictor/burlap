import unittest

import mock


class CommonTestCase(unittest.TestCase):

    #@mock.patch('burlap.user.run_as_root')
    def test_shellquote(self):
        from burlap.common import shellquote
        
        s = """# /etc/cron.d/anacron: crontab entries for the anacron package

SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

# minute hour day month weekday (0-6, 0 = Sunday) user command
 
*/5 * * * *   root    {command}
"""

        s = shellquote(s)
        