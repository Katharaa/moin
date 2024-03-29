# Copyright: 2006 MoinMoin:ThomasWaldmann
# Copyright: 2011 MoinMoin:ReimarBauer
# License: GNU GPL v2 (or any later version), see LICENSE.txt for details.

"""
MoinMoin - disable a user account
"""


from flask_script import Command, Option

from moin import user
from moin.app import before_wiki


class Disable_User(Command):
    description = 'This command allows you to disable user accounts.'
    option_list = (
        Option('--name', '-n', required=False, dest='name', type=str,
               help='Disable the user with user name NAME.'),
        Option('--uid', '-u', required=False, dest='uid', type=str,
               help='Disable the user with user id UID.'),
    )

    def run(self, name, uid):
        flags_given = name or uid
        if not flags_given:
            print('incorrect number of arguments')
            import sys
            sys.exit()

        before_wiki()
        if uid:
            u = user.User(uid)
        elif name:
            u = user.User(auth_username=name)

        if not u.exists():
            print('This user "{0!r}" does not exists!'.format(u.name))
            return

        print(" {0:<20} {1!r:<25} {2:<35}".format(u.itemid, u.name, u.email), end=' ')
        if not u.disabled:  # only disable once
            u.disabled = 1
            u.name = "{0}-{1}".format(u.name, u.id)
            if u.email:
                u.email = "{0}-{1}".format(u.email, u.id)
            u.subscriptions = []
            u.save()
            print("- disabled.")
        else:
            print("- is already disabled.")
