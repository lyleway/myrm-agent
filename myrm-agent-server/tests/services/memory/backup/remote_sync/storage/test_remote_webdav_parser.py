"""WebDAV directory listing parser: which entries count as backups and how their names are read.

[INPUT]
app.services.memory.backup.remote_sync.storage.webdav::_parse_webdav_multistatus

[OUTPUT]
Which PROPFIND entries become backup files, that each name comes back as it was uploaded, that every server's choice
of namespace prefix reads the same, and that an answer which is not a multistatus document raises.

[POS]
Pure parser tests on literal XML answers; no network and no strategy.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from app.services.memory.backup.remote_sync.storage import RemoteStorageError
from app.services.memory.backup.remote_sync.storage.webdav import _parse_webdav_multistatus

_NAME = "myrm.20250101120000.laptop.linux.json.gz"
_MODIFIED = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

# Answers in the shape each kind of server gives: the prefix that stands for DAV: differs, and so do the extras.
_APACHE = f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
<D:response xmlns:lp1="DAV:" xmlns:lp2="http://apache.org/dav/props/">
<D:href>/myrm-backups/{_NAME}</D:href>
<D:propstat><D:prop>
<lp1:getcontentlength>4096</lp1:getcontentlength>
<lp1:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</lp1:getlastmodified>
</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
</D:response>
</D:multistatus>"""
_NEXTCLOUD = f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" xmlns:oc="http://owncloud.org/ns">
<d:response>
<d:href>/remote.php/dav/files/alice/myrm-backups/{_NAME}</d:href>
<d:propstat><d:prop>
<d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
<d:getcontentlength>4096</d:getcontentlength>
</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
</d:response>
</d:multistatus>"""
_IIS = f"""<?xml version="1.0"?>
<a:multistatus xmlns:b="urn:uuid:c2f41010-65b3-11d1-a29f-00aa00c14882/" xmlns:a="DAV:">
<a:response>
<a:href>http://dav.example.test/myrm-backups/{_NAME}</a:href>
<a:propstat><a:status>HTTP/1.1 200 OK</a:status><a:prop>
<a:getcontentlength b:dt="int">4096</a:getcontentlength>
<a:getlastmodified b:dt="dateTime.rfc1123">Sat, 01 Jan 2025 12:00:00 GMT</a:getlastmodified>
</a:prop></a:propstat>
</a:response>
</a:multistatus>"""
_DEFAULT_NAMESPACE = f"""<multistatus xmlns="DAV:">
<response><href>/myrm-backups/{_NAME}</href>
<propstat><prop><getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</getlastmodified><getcontentlength>4096</getcontentlength></prop></propstat>
</response>
</multistatus>"""


class TestWebDAVXMLParser:
    @pytest.mark.parametrize(
        "answer", [_APACHE, _NEXTCLOUD, _IIS, _DEFAULT_NAMESPACE], ids=["apache", "nextcloud", "iis", "default-namespace"]
    )
    def test_every_prefix_for_dav_reads_the_same(self, answer: str):
        (backup,) = _parse_webdav_multistatus(answer)
        assert (backup.file_name, backup.size_bytes, backup.modified_time) == (_NAME, 4096, _MODIFIED)

    def test_a_property_the_server_lacks_is_an_empty_element_without_a_value(self):
        xml = f"""<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/myrm-backups/{_NAME}</d:href>
    <d:propstat><d:prop><d:getcontentlength/><d:getlastmodified/></d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>
    <d:propstat><d:prop><d:getcontentlength>4096</d:getcontentlength><d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
        (backup,) = _parse_webdav_multistatus(xml)
        assert (backup.size_bytes, backup.modified_time) == (4096, _MODIFIED)

    def test_only_properties_in_the_dav_namespace_count(self):
        xml = f"""<d:multistatus xmlns:d="DAV:" xmlns:x="urn:other">
  <d:response>
    <d:href>/myrm-backups/{_NAME}</d:href>
    <d:propstat><d:prop><x:getcontentlength>999</x:getcontentlength></d:prop></d:propstat>
  </d:response>
  <x:response><d:href>/myrm-backups/other.gz</d:href></x:response>
</d:multistatus>"""
        assert [(f.file_name, f.size_bytes) for f in _parse_webdav_multistatus(xml)] == [(_NAME, 0)]

    def test_a_name_with_an_escaped_ampersand_comes_back_unescaped(self):
        xml = """<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/myrm-backups/myrm.20250101120000.a&amp;b.linux.json.gz</d:href></d:response>
</d:multistatus>"""
        assert [f.file_name for f in _parse_webdav_multistatus(xml)] == ["myrm.20250101120000.a&b.linux.json.gz"]

    def test_an_href_the_server_puts_on_its_own_line_still_names_the_backup(self):
        xml = f"""<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>
    /myrm-backups/{_NAME}
  </d:href></d:response>
</d:multistatus>"""
        assert [f.file_name for f in _parse_webdav_multistatus(xml)] == [_NAME]

    def test_an_unreadable_date_is_the_time_of_the_listing_and_an_unreadable_size_is_zero(self):
        xml = f"""<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/myrm-backups/{_NAME}</d:href>
    <d:propstat><d:prop><d:getlastmodified>last Tuesday</d:getlastmodified><d:getcontentlength>many</d:getcontentlength></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
        before = datetime.now(UTC)
        (backup,) = _parse_webdav_multistatus(xml)
        assert before <= backup.modified_time <= datetime.now(UTC)
        assert backup.size_bytes == 0

    def test_a_date_without_a_zone_is_utc(self):
        xml = f"""<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/myrm-backups/{_NAME}</d:href>
    <d:propstat><d:prop><d:getlastmodified>01 Jan 2025 12:00:00 -0000</d:getlastmodified></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
        (backup,) = _parse_webdav_multistatus(xml)
        assert backup.modified_time == _MODIFIED

    @pytest.mark.parametrize(
        "answer",
        ["", "Bad Gateway", "<html><body>Bad Gateway</body></html>", "<d:multistatus xmlns:d='DAV:'><d:response>", "\x00"],
        ids=["empty", "plain-text", "html-page", "truncated", "binary"],
    )
    def test_an_answer_that_is_not_a_multistatus_document_raises_instead_of_listing_nothing(self, answer: str):
        with pytest.raises(RemoteStorageError, match="WebDAV listing"):
            _parse_webdav_multistatus(answer)

    def test_a_multistatus_that_lists_nothing_is_an_empty_listing(self):
        assert _parse_webdav_multistatus('<d:multistatus xmlns:d="DAV:"/>') == []

    def test_entity_expansion_is_refused(self):
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>'
            '<d:multistatus xmlns:d="DAV:"><d:response><d:href>&lol2;</d:href></d:response></d:multistatus>'
        )
        with pytest.raises(RemoteStorageError, match="WebDAV listing"):
            _parse_webdav_multistatus(bomb)

    @pytest.mark.parametrize(
        "hostile", ["<" * 50_000, "<d:response>" * 50_000, "<d:href>" * 50_000], ids=["angles", "responses", "hrefs"]
    )
    def test_a_hostile_answer_is_refused_in_linear_time(self, hostile: str):
        started = time.process_time()
        with pytest.raises(RemoteStorageError):
            _parse_webdav_multistatus(hostile)
        assert time.process_time() - started < 1.0

    def test_responses_nested_inside_each_other_are_not_listed_and_cost_linear_time(self):
        depth = 10_000
        xml = (
            '<d:multistatus xmlns:d="DAV:">'
            + "<d:response>" * depth
            + f"<d:href>/myrm-backups/{_NAME}</d:href>"
            + "</d:response>" * depth
            + "</d:multistatus>"
        )
        started = time.process_time()
        assert _parse_webdav_multistatus(xml) == []
        assert time.process_time() - started < 1.0

    def test_a_listing_of_tens_of_thousands_of_backups_is_read_in_linear_time(self):
        entries = "".join(
            f"<d:response><d:href>/myrm-backups/myrm.{index:014d}.laptop.linux.json.gz</d:href>"
            "<d:propstat><d:prop><d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>"
            "<d:getcontentlength>4096</d:getcontentlength></d:prop></d:propstat></d:response>"
            for index in range(20_000)
        )
        started = time.process_time()
        files = _parse_webdav_multistatus(f'<d:multistatus xmlns:d="DAV:">{entries}</d:multistatus>')
        assert len(files) == 20_000
        assert time.process_time() - started < 3.0

    def test_parse_valid_multistatus(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/myrm-backups/</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/myrm-backups/myrm.20250101120000.myhost.linux.json.gz</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
        <d:getcontentlength>4096</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
        result = _parse_webdav_multistatus(xml)
        assert len(result) == 1
        assert result[0].file_name == "myrm.20250101120000.myhost.linux.json.gz"
        assert result[0].size_bytes == 4096

    def test_parse_filters_non_gz_files(self):
        xml = """<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/backups/readme.txt</d:href>
    <d:propstat><d:prop>
      <d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>100</d:getcontentlength>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
        result = _parse_webdav_multistatus(xml)
        assert len(result) == 0

    def test_parse_returns_names_as_they_were_uploaded(self):
        xml = """<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/myrm-backups/myrm.20260101000000.%E5%B0%8F%E6%98%8E.linux.json.gz</d:href>
    <d:propstat><d:prop><d:getcontentlength>10</d:getcontentlength></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>http://dav.example.com/myrm-backups/myrm.20260101000000.my%20laptop.linux.json.gz</d:href>
    <d:propstat><d:prop><d:getcontentlength>10</d:getcontentlength></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
        names = [f.file_name for f in _parse_webdav_multistatus(xml)]
        assert names == ["myrm.20260101000000.小明.linux.json.gz", "myrm.20260101000000.my laptop.linux.json.gz"]

    def test_parse_skips_names_that_decode_into_a_path(self):
        xml = """<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/myrm-backups/..%2F..%2Fsecret.gz</d:href>
    <d:propstat><d:prop><d:getcontentlength>10</d:getcontentlength></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/myrm-backups/myrm.20260101000000.laptop.linux.json.gz</d:href>
    <d:propstat><d:prop><d:getcontentlength>10</d:getcontentlength></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
        names = [f.file_name for f in _parse_webdav_multistatus(xml)]
        assert names == ["myrm.20260101000000.laptop.linux.json.gz"]

    def test_parse_handles_missing_length(self):
        xml = """<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/backups/test.json.gz</d:href>
    <d:propstat><d:prop>
      <d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
        result = _parse_webdav_multistatus(xml)
        assert len(result) == 1
        assert result[0].size_bytes == 0
