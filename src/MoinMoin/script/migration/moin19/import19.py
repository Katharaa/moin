# Copyright: 2008 MoinMoin:JohannesBerg
# Copyright: 2008-2011 MoinMoin:ThomasWaldmann
# License: GNU GPL v2 (or any later version), see LICENSE.txt for details.

"""
MoinMoin - import content and user data from a moin 1.9 compatible storage
           into the moin2 storage.

TODO
----

* translate revno numbering into revid parents
* ACLs for attachments
"""


import os
import time
import re
import codecs
import hashlib
from StringIO import StringIO

from flask import current_app as app
from flask import g as flaskg
from flask_script import Command, Option

from ._utils19 import quoteWikinameFS, unquoteWikiname, split_body
from ._logfile19 import LogFile

from MoinMoin.constants.keys import *
from MoinMoin.constants.contenttypes import CONTENTTYPE_USER
from MoinMoin.constants.itemtypes import ITEMTYPE_DEFAULT
from MoinMoin.constants.namespaces import NAMESPACE_DEFAULT, NAMESPACE_USERPROFILES
from MoinMoin.storage.error import NoSuchRevisionError
from MoinMoin.util.mimetype import MimeType
from MoinMoin.util.crypto import make_uuid
from MoinMoin import security
from MoinMoin.converter.moinwiki19_in import ConverterFormat19 as conv_in
from MoinMoin.converter.moinwiki_out import Converter as conv_out

from MoinMoin import log
logging = log.getLogger(__name__)


UID_OLD = 'old_user_id'  # dynamic field *_id, so we don't have to change schema

CHARSET = 'utf-8'

ACL_RIGHTS_CONTENTS = ['read', 'write', 'create', 'destroy', 'admin', ]

DELETED_MODE_KEEP = 'keep'
DELETED_MODE_KILL = 'kill'

CONTENTTYPE_DEFAULT = u'text/plain;charset=utf-8'
CONTENTTYPE_MOINWIKI = u'text/x.moin.wiki;format=1.9;charset=utf-8'
FORMAT_TO_CONTENTTYPE = {
    'wiki': CONTENTTYPE_MOINWIKI,
    'text/wiki': CONTENTTYPE_MOINWIKI,
    'text/moin-wiki': CONTENTTYPE_MOINWIKI,
    'creole': u'text/x.moin.creole;charset=utf-8',
    'python': u'text/x-python;charset=utf-8',
    'text/creole': u'text/x.moin.creole;charset=utf-8',
    'rst': u'text/rst;charset=utf-8',
    'text/rst': u'text/rst;charset=utf-8',
    'plain': u'text/plain;charset=utf-8',
    'text/plain': u'text/plain;charset=utf-8',
}

last_moin19_rev = {}
user_names = []


class ImportMoin19(Command):
    description = 'Import data from a moin 1.9 wiki.'

    option_list = [
        Option('--data_dir', '-d', dest='data_dir', type=unicode, required=True,
               help='moin 1.9 data_dir (contains pages and users subdirectories).'),
        Option('-i', '--index-create', action='store_true', dest='create_index',
               required=False, default=False),
        Option('-s', '--storage-create', action='store_true', dest='create_storage',
               required=False, default=False),
    ]

    def run(self, data_dir=None):
        flaskg.add_lineno_attr = False
        flaskg.item_name2id = {}
        userid_old2new = {}
        indexer = app.storage
        backend = indexer.backend  # backend without indexing

        print "\nConverting Users...\n"
        for rev in UserBackend(os.path.join(data_dir, 'user')):  # assumes user/ below data_dir
            global user_names
            user_names.append(rev.meta['name'][0])
            userid_old2new[rev.uid] = rev.meta['itemid']  # map old userid to new userid
            backend.store(rev.meta, rev.data)

        print "\nConverting Pages/Attachments...\n"
        for rev in PageBackend(data_dir, deleted_mode=DELETED_MODE_KILL, default_markup=u'wiki'):
            for user_name in user_names:
                if rev.meta['name'][0] == user_name or rev.meta['name'][0].startswith(user_name + '/'):
                    rev.meta['namespace'] = u'users'
                    break
            backend.store(rev.meta, rev.data)
            # item_name to itemid xref required for migrating user subscriptions
            flaskg.item_name2id[rev.meta['name'][0]] = rev.meta['itemid']

        print "\nConverting Revision Editors...\n"
        for mountpoint, revid in backend:
            meta, data = backend.retrieve(mountpoint, revid)
            if USERID in meta:
                try:
                    meta[USERID] = userid_old2new[meta[USERID]]
                except KeyError:
                    # user profile lost, but userid referred by revision
                    print (u"Missing userid {0!r}, editor of {1} revision {2}".format(meta[USERID], meta[NAME][0], revid)).encode('utf-8')
                    del meta[USERID]
                backend.store(meta, data)
            elif meta.get(CONTENTTYPE) == CONTENTTYPE_USER:
                meta.pop(UID_OLD, None)  # not needed any more
                backend.store(meta, data)

        print "\nConverting last revision of Moin 1.9 items to Moin 2.0"
        self.conv_in = conv_in()
        self.conv_out = conv_out()
        for item_name, (revno, namespace) in sorted(last_moin19_rev.items()):
            print '    Processing item "{0}", namespace "{1}", revision "{2}"'.format(item_name, namespace, revno)
            if namespace == u'':
                namespace = u'default'
            meta, data = backend.retrieve(namespace, revno)
            data_in = ''.join(data.readlines())
            dom = self.conv_in(data_in, 'text/x.moin.wiki;format=1.9;charset=utf-8')
            out = self.conv_out(dom)
            out = out.encode(CHARSET)
            size, hash_name, hash_digest = hash_hexdigest(out)
            out = StringIO(out)
            meta[hash_name] = hash_digest
            meta[SIZE] = size
            meta[REVID] = make_uuid()
            meta[REV_NUMBER] = meta[REV_NUMBER] + 1
            meta[MTIME] = int(time.time())
            meta[COMMENT] = 'Convert moin 1.9 markup to 2.0'
            meta[CONTENTTYPE] = 'text/x.moin.wiki;charset=utf-8'
            del meta['dataid']
            out.seek(0)
            backend.store(meta, out)

        print "\nRebuilding the index..."
        indexer.close()
        indexer.destroy()
        indexer.create()
        indexer.rebuild()
        indexer.open()

        print "Finished conversion!"


class KillRequested(Exception):
    """raised if item killing is requested by DELETED_MODE"""


class PageBackend(object):
    """
    moin 1.9 page directory
    """
    def __init__(self, path, deleted_mode=DELETED_MODE_KEEP,
                 default_markup=u'wiki',
                 item_category_regex=ur'(?P<all>Category(?P<key>(?!Template)\S+))'):
        """
        :param path: storage path (data_dir)
        :param deleted_mode: 'kill' - just ignore deleted pages (pages with
                                      non-existing current revision) and their attachments
                                      as if they were not there.
                                      Non-deleted pages (pages with an existing current
                                      revision) that have non-current deleted revisions
                                      will be treated as for 'keep'.
                             'keep' - keep deleted pages as items with empty revisions,
                                      keep their attachments. (default)
        :param default_markup: used if a page has no #format line, moin 1.9's default
                               'wiki' and we also use this default here.
        """
        self._path = path
        assert deleted_mode in (DELETED_MODE_KILL, DELETED_MODE_KEEP, )
        self.deleted_mode = deleted_mode
        self.format_default = default_markup
        self.item_category_regex = re.compile(item_category_regex, re.UNICODE)

    def __iter__(self):
        pages_dir = os.path.join(self._path, 'pages')
        for f in os.listdir(pages_dir):
            itemname = unquoteWikiname(f)
            try:
                item = PageItem(self, os.path.join(pages_dir, f), itemname)
            except KillRequested:
                pass  # a message was already output
            except (IOError, AttributeError):
                print (u"    >> Error: {0} is missing file 'current' or 'edit-log'".format(os.path.normcase(os.path.join(pages_dir, f)))).encode('utf-8')
            except Exception as err:
                logging.exception((u"PageItem {0!r} raised exception:".format(itemname))).encode('utf-8')
            else:
                for rev in item.iter_revisions():
                    yield rev
                for rev in item.iter_attachments():
                    yield rev


class PageItem(object):
    """
    moin 1.9 page
    """
    def __init__(self, backend, path, itemname):
        self.backend = backend
        self.name = itemname
        self.path = path
        print (u"Processing item {0}".format(itemname)).encode('utf-8')
        currentpath = os.path.join(self.path, 'current')
        with open(currentpath, 'r') as f:
            self.current = int(f.read().strip())
        editlogpath = os.path.join(self.path, 'edit-log')
        self.editlog = EditLog(editlogpath)
        self.acl = None  # TODO
        self.itemid = make_uuid()
        if backend.deleted_mode == DELETED_MODE_KILL:
            revpath = os.path.join(self.path, 'revisions', '{0:08d}'.format(self.current))
            if not os.path.exists(revpath):
                print (u"    >> Deleted item not migrated: {0}, last revision no: {1}".format(itemname, self.current)).encode('utf-8')
                raise KillRequested('deleted_mode wants killing/ignoring')

    def iter_revisions(self):
        revisionspath = os.path.join(self.path, 'revisions')
        try:
            # rather use this or a range(1, self.current+1)?
            fnames = os.listdir(revisionspath)
        except OSError:
            fnames = []
        for fname in fnames:
            try:
                revno = int(fname)
                yield PageRevision(self, revno, os.path.join(revisionspath, fname))
            except Exception as err:
                logging.exception("PageRevision {0!r} {1!r} raised exception:".format(self.name, fname))

    def iter_attachments(self):
        attachmentspath = os.path.join(self.path, 'attachments')
        try:
            fnames = os.listdir(attachmentspath)
        except OSError:
            fnames = []
        for fname in fnames:
            attachname = fname.decode('utf-8')
            try:
                yield AttachmentRevision(self.name, attachname, os.path.join(attachmentspath, fname),
                                         self.editlog, self.acl)
            except Exception as err:
                logging.exception("AttachmentRevision {0!r}/{1!r} raised exception:".format(self.name, attachname))


class PageRevision(object):
    """
    moin 1.9 page revision
    """
    def __init__(self, item, revno, path):
        item_name = item.name
        itemid = item.itemid
        editlog = item.editlog
        self.backend = item.backend
        editlog.to_begin()
        # we just read the page and parse it here, makes the rest of the code simpler:
        try:
            with codecs.open(path, 'r', CHARSET) as f:
                content = f.read()
        except (IOError, OSError):
            # handle deleted revisions (for all revnos with 0<=revno<=current) here
            # we prepare some values for the case we don't find a better value in edit-log:
            meta = {MTIME: -1,  # fake, will get 0 in the end
                    NAME: [item_name],  # will get overwritten with name from edit-log
                                        # if we have an entry there
                   }
            try:
                revpath = os.path.join(item.path, 'revisions', '{0:08d}'.format(revno - 1))
                previous_meta = PageRevision(item, revno - 1, revpath).meta
                # if this page revision is deleted, we have no on-page metadata.
                # but some metadata is required, thus we have to copy it from the
                # (non-deleted) revision revno-1:
                for key in [ACL, NAME, CONTENTTYPE, MTIME, ]:
                    if key in previous_meta:
                        meta[key] = previous_meta[key]
            except NoSuchRevisionError:
                pass  # should not happen
            meta[MTIME] += 1  # it is now either 0 or prev rev mtime + 1
            data = u''
            try:
                editlog_data = editlog.find_rev(revno)
            except KeyError:
                print (u"    >> Missing edit log data item = {0}, revision = {1}".format(item_name, revno)).encode('utf-8')
                if 0 <= revno <= item.current:
                    editlog_data = {  # make something up
                        ACTION: u'SAVE/DELETE',
                    }
                else:
                    raise NoSuchRevisionError('Item {0!r} has no revision {1} (not even a deleted one)!'.format(
                                              item.name, revno))
        else:
            try:
                editlog_data = editlog.find_rev(revno)
            except KeyError:
                print (u"    >> Missing edit log data, name = {0}, revision = {1}".format(item_name, revno)).encode('utf-8')
                if 1 <= revno <= item.current:
                    editlog_data = {  # make something up
                        NAME: [item.name],
                        MTIME: int(os.path.getmtime(path)),
                        ACTION: ACTION_SAVE,
                    }
            meta, data = split_body(content)
        meta.update(editlog_data)
        format = meta.pop('format', self.backend.format_default)
        meta[CONTENTTYPE] = FORMAT_TO_CONTENTTYPE.get(format, CONTENTTYPE_DEFAULT)
        data = self._process_data(meta, data)
        data = data.encode(CHARSET)
        size, hash_name, hash_digest = hash_hexdigest(data)
        meta[hash_name] = hash_digest
        meta[SIZE] = size
        meta[ITEMID] = itemid
        meta[REVID] = make_uuid()
        meta[REV_NUMBER] = revno
        meta[NAMESPACE] = NAMESPACE_DEFAULT
        meta[ITEMTYPE] = ITEMTYPE_DEFAULT
        if meta[NAME][0].endswith('Template'):
            if TAGS in meta:
                meta[TAGS].append(TEMPLATE)
            else:
                meta[TAGS] = [TEMPLATE]
        self.meta = {}
        for k, v in meta.iteritems():
            if isinstance(v, list):
                v = tuple(v)
            self.meta[k] = v
        self.data = StringIO(data)

        acl_line = self.meta.get(ACL)
        if acl_line is not None:
            self.meta[ACL] = regenerate_acl(acl_line)

        for user_name in user_names:
            if meta['name'][0] == user_name or meta['name'][0].startswith(user_name + '/'):
                meta['namespace'] = u'users'
                break

        print (u"    Processed revision {0} of item {1}, revid = {2}, contenttype = {3}".format(revno,
               item_name, meta[REVID], meta[CONTENTTYPE])).encode('utf-8')

        global last_moin19_rev
        if meta[CONTENTTYPE] == CONTENTTYPE_MOINWIKI:
            last_moin19_rev[item_name] = (meta[REVID], meta[NAMESPACE])

    def _process_data(self, meta, data):
        """ In moin 1.x markup, not all metadata is stored in the page's header.
            E.g. categories are stored in the footer of the page content. For
            moin2, we extract that stuff from content and put it into metadata.
        """
        if meta[CONTENTTYPE] == CONTENTTYPE_MOINWIKI:
            data = process_categories(meta, data, self.backend.item_category_regex)
        return data


def process_categories(meta, data, item_category_regex):
    # process categories to tags
    # find last ---- in the data plus the categories below it
    m = re.search(r'\n\r?\s*-----*', data[::-1])
    if m:
        start = m.start()
        end = m.end()
        # categories are after the ---- line
        if start > 0:
            categories = data[-start:]
        else:
            categories = u''
        if categories:
            # for CategoryFoo, group 'all' matches CategoryFoo, group 'key' matches just Foo
            # we use 'all' so we don't need to rename category items
            matches = list(item_category_regex.finditer(categories))
            if matches:
                data = data[:-end]  # remove the ---- line from the content
                tags = [m.group('all') for m in matches]
                meta.setdefault(TAGS, []).extend(tags)
                # remove everything between first and last category from the content
                # unexpected text before and after categories survives, any text between categories is deleted
                start = matches[0].start()
                end = matches[-1].end()
                print '    Converted Categories to Tags: {0}'.format(tags)
                rest = categories[:start] + categories[end:]
                data += u'\r\n' + rest.lstrip()
        data = data.rstrip() + u'\r\n'
    return data


class AttachmentRevision(object):
    """
    moin 1.9 attachment (there is no revisioning, just 1 revision per attachment)
    """
    def __init__(self, item_name, attach_name, attpath, editlog, acl):
        try:
            meta = editlog.find_attach(attach_name)
        except KeyError:
            meta = {  # make something up
                MTIME: int(os.path.getmtime(attpath)),
                ACTION: ACTION_SAVE,
            }
        meta[NAME] = [u'{0}/{1}'.format(item_name, attach_name)]
        if acl is not None:
            meta[ACL] = acl
        meta[CONTENTTYPE] = unicode(MimeType(filename=attach_name).content_type())
        f = open(attpath, 'rb')
        size, hash_name, hash_digest = hash_hexdigest(f)
        f.seek(0)
        self.data = f
        meta[hash_name] = hash_digest
        meta[SIZE] = size
        meta[ITEMID] = make_uuid()
        meta[REVID] = make_uuid()
        meta[REV_NUMBER] = 1
        meta[ITEMTYPE] = ITEMTYPE_DEFAULT
        self.meta = meta


class EditLog(LogFile):
    """ Access the edit-log and return metadata as the new api wants it. """
    def __init__(self, filename, buffer_size=4096):
        LogFile.__init__(self, filename, buffer_size)
        self._NUM_FIELDS = 9

    def parser(self, line):
        """ Parse edit-log line into fields """
        fields = line.strip().split(u'\t')
        fields = (fields + [u''] * self._NUM_FIELDS)[:self._NUM_FIELDS]
        keys = (MTIME, '__rev', ACTION, NAME, ADDRESS, HOSTNAME, USERID, EXTRA, COMMENT)
        result = dict(zip(keys, fields))
        # do some conversions/cleanups/fallbacks:
        result[MTIME] = int(long(result[MTIME] or 0) / 1000000)  # convert usecs to secs
        result['__rev'] = int(result['__rev'])
        result[NAME] = [unquoteWikiname(result[NAME])]
        action = result[ACTION]
        extra = result[EXTRA]
        if extra:
            if action.startswith('ATT'):
                result[NAME] += u'/' + extra  # append filename to pagename
                # keep EXTRA for find_attach
            elif action == 'SAVE/RENAME':
                if extra:
                    result[NAME_OLD] = extra
                del result[EXTRA]
                result[ACTION] = ACTION_RENAME
            elif action == 'SAVE/REVERT':
                if extra:
                    result[REVERTED_TO] = int(extra)
                del result[EXTRA]
                result[ACTION] = ACTION_REVERT
        userid = result[USERID]
        # TODO
        # if userid:
        #    result[USERID] = self.idx.user_uuid(old_id=userid, refcount=True)
        return result

    def find_rev(self, revno):
        """ Find metadata for some revno revision in the edit-log. """
        for meta in self:
            if meta['__rev'] == revno:
                break
        else:
            raise KeyError
        del meta['__rev']
        meta = dict([(k, v) for k, v in meta.items() if v])  # remove keys with empty values
        if meta.get(ACTION) == u'SAVENEW':
            # replace SAVENEW with just SAVE
            meta[ACTION] = ACTION_SAVE
        return meta

    def find_attach(self, attachname):
        """ Find metadata for some attachment name in the edit-log. """
        for meta in self.reverse():  # use reverse iteration to get the latest upload's data
            if (meta['__rev'] == 99999999 and
                    meta[ACTION] == 'ATTNEW' and
                    meta[EXTRA] == attachname):
                break
        else:
            self.to_end()
            raise KeyError
        del meta['__rev']
        del meta[EXTRA]  # we have full name in NAME
        meta[ACTION] = ACTION_SAVE
        meta = dict([(k, v) for k, v in meta.items() if v])  # remove keys with empty values
        return meta


def regenerate_acl(acl_string, acl_rights_valid=ACL_RIGHTS_CONTENTS):
    """ recreate ACL string to remove invalid rights """
    assert isinstance(acl_string, unicode)
    result = []
    for modifier, entries, rights in security.ACLStringIterator(acl_rights_valid, acl_string):
        if (entries, rights) == (['Default'], []):
            result.append("Default")
        else:
            result.append("{0}{1}:{2}".format(
                          modifier,
                          u','.join(entries),
                          u','.join(rights)  # iterator has removed invalid rights
                         ))
    result = u' '.join(result)
    logging.debug("regenerate_acl {0!r} -> {1!r}".format(acl_string, result))
    return result


def _decode_list(line):
    """
    Decode list of items from user data file

    :param line: line containing list of items, encoded with _encode_list
    :rtype: list of unicode strings
    :returns: list of items in encoded in line
    """
    items = [item.strip() for item in line.split('\t')]
    items = [item for item in items if item]
    return tuple(items)


def _decode_dict(line):
    """
    Decode dict of key:value pairs from user data file

    :param line: line containing a dict, encoded with _encode_dict
    :rtype: dict
    :returns: dict  unicode:unicode items
    """
    items = [item.strip() for item in line.split('\t')]
    items = [item for item in items if item]
    items = [item.split(':', 1) for item in items]
    return dict(items)


class UserRevision(object):
    """
    moin 1.9 user
    """
    def __init__(self, path, uid):
        self.path = path
        self.uid = uid
        meta = self._process_usermeta(self._parse_userprofile())
        meta[CONTENTTYPE] = CONTENTTYPE_USER
        meta[UID_OLD] = uid
        meta[ITEMID] = make_uuid()
        meta[REVID] = make_uuid()
        meta[SIZE] = 0
        meta[ACTION] = ACTION_SAVE
        self.meta = meta
        self.data = StringIO('')

    def _parse_userprofile(self):
        with codecs.open(os.path.join(self.path, self.uid), "r", CHARSET) as meta_file:
            metadata = {}
            for line in meta_file:
                if line.startswith('#') or line.strip() == "":
                    continue
                key, value = line.strip().split('=', 1)
                # Decode list values
                if key.endswith('[]'):
                    key = key[:-2]
                    value = _decode_list(value)

                # Decode dict values
                elif key.endswith('{}'):
                    key = key[:-2]
                    value = _decode_dict(value)

                metadata[key] = value
        return metadata

    def _process_usermeta(self, metadata):
        # stuff we want to have stored as boolean:
        bool_defaults = [  # taken from cfg.checkbox_defaults
            (SHOW_COMMENTS, 'False'),
            (EDIT_ON_DOUBLECLICK, 'True'),
            (SCROLL_PAGE_AFTER_EDIT, 'True'),
            (WANT_TRIVIAL, 'False'),
            (MAILTO_AUTHOR, 'False'),
            (DISABLED, 'False'),
        ]
        for key, default in bool_defaults:
            metadata[key] = metadata.get(key, default) in ['True', 'true', '1']

        # stuff we want to have stored as integer:
        int_defaults = [
            (EDIT_ROWS, '0'),
        ]
        for key, default in int_defaults:
            metadata[key] = int(metadata.get(key, default))

        metadata[NAMESPACE] = NAMESPACE_USERPROFILES
        metadata[NAME] = [metadata[NAME]]

        # rename last_saved to MTIME, int MTIME should be enough:
        metadata[MTIME] = int(float(metadata.get('last_saved', '0')))

        # rename aliasname to display_name:
        metadata[DISPLAY_NAME] = metadata.get('aliasname')

        print (u"    Processing user {0} {1} {2}".format(metadata['name'][0], self.uid, metadata['email'])).encode('utf-8')

        # transfer subscribed_pages to subscription_patterns
        metadata[SUBSCRIPTIONS] = self.migrate_subscriptions(metadata.get('subscribed_pages', []))

        # convert bookmarks from usecs (and str) to secs (int)
        metadata[BOOKMARKS] = [(interwiki, int(long(bookmark) / 1000000))
                               for interwiki, bookmark in metadata.get('bookmarks', {}).items()]

        # stuff we want to get rid of:
        kill = ['aliasname',  # renamed to display_name
                'real_language',  # crap (use 'language')
                'wikiname_add_spaces',  # crap magic (you get it like it is)
                'recoverpass_key',  # user can recover again if needed
                'editor_default',  # not used any more
                'editor_ui',  # not used any more
                'external_target',  # ancient, not used any more
                'passwd',  # ancient, not used any more (use enc_password)
                'show_emoticons',  # ancient, not used any more
                'show_fancy_diff',  # kind of diff display now depends on mimetype
                'show_fancy_links',  # not used any more (now link rendering depends on theme)
                'show_toolbar',  # not used any more
                'show_topbottom',  # crap
                'show_nonexist_qm',  # crap, can be done by css
                'show_page_trail',  # theme decides whether to show trail
                'remember_last_visit',  # we show trail, user can click there
                'remember_me',  # don't keep sessions open for a long time
                'subscribed_pages',  # renamed to subscribed_items
                'edit_cols',  # not used any more
                'jid',  # no jabber support
                'tz_offset',  # we have real timezone now
                'date_fmt',  # not used any more
                'datetime_fmt',  # not used any more
                'last_saved',  # renamed to MTIME
                'email_subscribed_events',  # XXX no support yet
                'jabber_subscribed_events',  # XXX no support yet
               ]
        for key in kill:
            if key in metadata:
                del metadata[key]

        # finally, remove some empty values (that have empty defaults anyway or
        # make no sense when empty):
        empty_kill = ['aliasname', DISPLAY_NAME, BOOKMARKS, ENC_PASSWORD,
                      'language', CSS_URL, EMAIL, ]  # XXX check subscribed_items, quicklinks
        for key in empty_kill:
            if key in metadata and metadata[key] in [u'', tuple(), {}, [], ]:
                del metadata[key]

        # moin2 only supports passlib generated hashes, drop everything else
        # (users need to do pw recovery in case they are affected)
        pw = metadata.get(ENC_PASSWORD)
        if pw is not None:
            if pw.startswith('{PASSLIB}'):
                # take it, but strip the prefix as moin2 does not use that any more
                metadata[ENC_PASSWORD] = pw[len('{PASSLIB}'):]
            else:
                # drop old, unsupported (and also more or less unsafe) hashing scheme
                del metadata[ENC_PASSWORD]

        # TODO quicklinks and subscribed_items - check for non-interwiki elements and convert them to interwiki

        return metadata

    def migrate_subscriptions(self, subscribed_items):
        """ Transfer subscribed_items meta to subscriptions meta

        WikiFarmNames are converted to namespace names.

        :param subscribed_items: a list of moin19-format subscribed_items
        :return: subscriptions
        """
        RECHARS = ur'.^$*+?{\|('
        subscriptions = []
        for subscribed_item in subscribed_items:
            print (u"        User is subscribed to {0}".format(subscribed_item)).encode('utf-8')
            if flaskg.item_name2id.get(subscribed_item):
                subscriptions.append("{0}:{1}".format(ITEMID, flaskg.item_name2id.get(subscribed_item)))
            else:
                wikiname = ""
                if ":" in subscribed_item:
                    wikiname, subscribed_item = subscribed_item.split(":", 1)

                if subscribed_item.endswith(".*") and len(subscribed_item) > 2 and not any(x in subscribed_item[:-2] for x in RECHARS):
                    subscriptions.append("{0}:{1}:{2}".format(NAMEPREFIX, wikiname, subscribed_item[:-2]))
                else:
                    subscriptions.append("{0}:{1}:{2}".format(NAMERE, wikiname, subscribed_item))

        return subscriptions


class UserBackend(object):
    """
    moin 1.9 user directory
    """
    def __init__(self, path):
        """
        :param path: user_dir path
        """
        self.path = path

    def __iter__(self):
        user_re = re.compile(r'^\d+\.\d+(\.\d+)?$')
        for uid in os.listdir(self.path):
            if user_re.match(uid):
                try:
                    rev = UserRevision(self.path, uid)
                except Exception as err:
                    logging.exception("Exception in user item processing {0}".format(uid))
                else:
                    yield rev


def hash_hexdigest(content, bufsize=4096):
    size = 0
    hash = hashlib.new(HASH_ALGORITHM)
    if hasattr(content, "read"):
        while True:
            buf = content.read(bufsize)
            hash.update(buf)
            size += len(buf)
            if not buf:
                break
    elif isinstance(content, str):
        hash.update(content)
        size = len(content)
    else:
        raise ValueError("unsupported content object: {0!r}".format(content))
    return size, HASH_ALGORITHM, unicode(hash.hexdigest())