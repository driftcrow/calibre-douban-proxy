#!/usr/bin/env python2
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai  -*- origami-fold-style: triple-braces; -*-

from __future__ import absolute_import, division, print_function, unicode_literals

__license__   = 'GPL v3'
__copyright__ = '2011, Kovid Goyal <kovid@kovidgoyal.net>; 2011, Li Fanxi <lifanxi@freemindworld.com>'
__docformat__ = 'restructuredtext en'

import time
from functools import partial
try:
    from queue import Empty, Queue
except ImportError:
    from Queue import Empty, Queue


from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.sources.base import Option, Source
from calibre.ebooks.metadata.book.base import Metadata
from calibre import as_unicode

NAMESPACES = {
              'openSearch':'http://a9.com/-/spec/opensearchrss/1.0/',
              'atom' : 'http://www.w3.org/2005/Atom',
              'db': 'https://www.douban.com/xmlns/',
              'gd': 'http://schemas.google.com/g/2005'
            }


def get_details(browser, url, timeout):  # {{{
    try:
        if Douban.DOUBAN_API_KEY and Douban.DOUBAN_API_KEY != '':
            url = url + "?apikey=" + Douban.DOUBAN_API_KEY
        raw = browser.open_novisit(url, timeout=timeout).read()
    except Exception as e:
        gc = getattr(e, 'getcode', lambda : -1)
        if gc() != 403:
            raise
        # Douban is throttling us, wait a little
        time.sleep(2)
        raw = browser.open_novisit(url, timeout=timeout).read()

    return raw
# }}}


class Douban(Source):

    name = 'Douban Books Proxy'
    author = 'Li Fanxi & Driftcrow'
    version = (2, 1, 2)
    minimum_calibre_version = (2, 80, 0)

    description = _('Downloads metadata and covers from Douban.com. '
            'Useful only for Chinese language books.')

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset(['title', 'authors', 'tags',
        'pubdate', 'comments', 'publisher', 'identifier:isbn', 'rating',
        'identifier:douban'])  # language currently disabled
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True

    DOUBAN_API_KEY = '0bd1672394eb1ebf2374356abec15c3d'
    DOUBAN_BOOK_URL = 'https://book.douban.com/subject/%s/'
    DOUBAN_BOOK_PROXY = 'https://douban.uieee.com/v2/book/'
    # SEARCH_URL = 'https://douban.uieee.com/v2/book/search?'

    options = (
        Option('include_subtitle_in_title', 'bool', True, _('Include subtitle in book title:'),
               _('Whether to append subtitle in the book title.')),
    )

    def to_metadata(self, browser, log, entry_, timeout):  # {{{
        from lxml import etree
        from calibre.ebooks.chardet import xml_to_unicode
        from calibre.utils.date import parse_date, utcnow
        from calibre.utils.cleantext import clean_ascii_chars

        # log.info('entry_ is: ',entry_)
        id_url = entry_['url']
        douban_id = entry_['id']
        title_ = entry_['title']
        subtitle = entry_['subtitle']
        authors = [x.strip() for x in entry_['author'] if x]
        if not authors:
            authors = [_('Unknown')]

        mi = Metadata(title_, authors)
        mi.identifiers = {'douban':douban_id}
        mi.comments = entry_['summary']
        mi.publisher = entry_['publisher']

        # ISBN
        mi.isbn = entry_['isbn10']
        mi.all_isbns = [entry_['isbn10'],entry_['isbn13']]

        # Tags
        mi.tags = [x['name'].strip() for x in entry_['tags']]

        # pubdate
        pubdate = entry_['pubdate']
        if pubdate:
            try:
                default = utcnow().replace(day=15)
                mi.pubdate = parse_date(pubdate, assume_utc=True, default=default)
            except:
                log.error('Failed to parse pubdate %r'%pubdate)


        # Ratings
        mi.rating = float(entry_['rating']['average']) / 2.0

        # Cover
        mi.has_douban_cover = entry_['image']
        return mi
    # }}}

    def get_book_url(self, identifiers):  # {{{
        db = identifiers.get('douban', None)
        if db is not None:
            return ('douban', db, self.DOUBAN_BOOK_URL%db)
    # }}}

    def create_query(self, log, title=None, authors=None, identifiers={}):  # {{{
        try:
            from urllib.parse import urlencode
        except ImportError:
            from urllib import urlencode
        SEARCH_URL = self.DOUBAN_BOOK_PROXY + 'search?'
        ISBN_URL = self.DOUBAN_BOOK_PROXY + 'isbn/'
        SUBJECT_URL = self.DOUBAN_BOOK_PROXY + 'subject/'

        q = ''
        t = None
        isbn = check_isbn(identifiers.get('isbn', None))
        subject = identifiers.get('douban', None)
        if isbn is not None:
            q = isbn
            t = 'isbn'
        elif subject is not None:
            q = subject
            t = 'subject'
        elif title or authors:
            def build_term(prefix, parts):
                return ' '.join(x for x in parts)
            title_tokens = list(self.get_title_tokens(title))
            if title_tokens:
                q += build_term('title', title_tokens)
            author_tokens = list(self.get_author_tokens(authors,
                    only_first_author=True))
            if author_tokens:
                q += ((' ' if q != '' else '') +
                    build_term('author', author_tokens))
            t = 'search'
        q = q.strip()
        if isinstance(q, type(u'')):
            q = q.encode('utf-8')
        if not q:
            return None
        url = None
        if t == "isbn":
            url = ISBN_URL + q
        elif t == 'subject':
            url = SUBJECT_URL + q
        else:
            url = SEARCH_URL + urlencode({
                    'q': q,
                    })
        # if self.DOUBAN_API_KEY and self.DOUBAN_API_KEY != '':
        #     if t == "isbn" or t == "subject":
        #         url = url + "?apikey=" + self.DOUBAN_API_KEY
        #     else:
        #         url = url + "&apikey=" + self.DOUBAN_API_KEY
        return url
    # }}}

    def download_cover(self, log, result_queue, abort,  # {{{
            title=None, authors=None, identifiers={}, timeout=30, get_best_cover=False):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(log, rq, abort, title=title, authors=authors,
                    identifiers=identifiers)
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(key=self.identify_results_keygen(
                title=title, authors=authors, identifiers=identifiers))
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return

        if abort.is_set():
            return
        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)

    # }}}

    def get_cached_cover_url(self, identifiers):  # {{{
        url = None
        db = identifiers.get('douban', None)
        if db is None:
            isbn = identifiers.get('isbn', None)
            if isbn is not None:
                db = self.cached_isbn_to_identifier(isbn)
        if db is not None:
            url = self.cached_identifier_to_cover_url(db)

        return url
    # }}}

    def get_all_details(self, br, log, entries, abort,  # {{{
            result_queue, timeout):
        # for relevance, i in enumerate(entries):
        for  i in entries:
            try:
                ans = self.to_metadata(br, log, i, timeout)
                if isinstance(ans, Metadata):
                    # ans.source_relevance = relevance
                    db = ans.identifiers['douban']
                    for isbn in getattr(ans, 'all_isbns', []):
                        self.cache_isbn_to_identifier(isbn, db)
                    if ans.has_douban_cover:
                        self.cache_identifier_to_cover_url(db,
                                ans.has_douban_cover)
                    self.clean_downloaded_metadata(ans)

                    result_queue.put(ans)
            except:
                log.exception(
                    'Failed to get metadata for identify entry:',
                    i
                )
            if abort.is_set():
                break
    # }}}

    def identify(self, log, result_queue, abort, title=None, authors=None,  # {{{
            identifiers={}, timeout=30):
        import json
        from calibre.ebooks.chardet import xml_to_unicode
        from calibre.utils.cleantext import clean_ascii_chars

        # XPath = partial(etree.XPath, namespaces=NAMESPACES)
        # entry          = XPath('//atom:entry')

        query = self.create_query(log, title=title, authors=authors,
                identifiers=identifiers)
        if not query:
            log.error('Insufficient metadata to construct query')
            return
        br = self.browser
        try:
            raw = br.open_novisit(query, timeout=timeout).read()
        except Exception as e:
            log.exception('Failed to make identify query: %r'%query)
            return as_unicode(e)
        try:
            # parser = etree.XMLParser(recover=True, no_network=True)
            # log.info('parser is ', parser)
            # feed = etree.fromstring(xml_to_unicode(clean_ascii_chars(raw),
            #     strip_encoding_pats=True)[0], parser=parser)

            # log.info('feed is ', feed)
            # entries = entry(feed)
            entries = []
            data = json.loads(raw)
            if  data.has_key('books'):
                entries = data['books']
            else:
                entries.append(data)
        except Exception as e:
            log.exception('Failed to parse identify results')
            return as_unicode(e)
        if not entries and identifiers and title and authors and \
                not abort.is_set():
            return self.identify(log, result_queue, abort, title=title,
                    authors=authors, timeout=timeout)

        # There is no point running these queries in threads as douban
        # throttles requests returning 403 Forbidden errors
        self.get_all_details(br, log, entries, abort, result_queue, timeout)

        return None
    # }}}


if __name__ == '__main__':  # tests {{{
    # To run these test use: calibre-debug -e src/calibre/ebooks/metadata/sources/douban.py
    from calibre.ebooks.metadata.sources.test import (test_identify_plugin,
            title_test, authors_test)
    test_identify_plugin(Douban.name,
        [
            (
                {'identifiers':{'isbn': '9787536692930'}, 'title':'三体',
                    'authors':['刘慈欣']},
                [title_test('三体', exact=True),
                    authors_test(['刘慈欣'])]
            ),

            (
                {'title': 'Linux内核修炼之道', 'authors':['任桥伟']},
                [title_test('Linux内核修炼之道', exact=False)]
            ),
    ])
# }}}
