from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import httpx



def attachment(name, identifier='one'):
    return {'rel': 'AttachedFile', 'url': 'https://tfs.test/DefaultCollection/_apis/wit/attachments/'+identifier,
            'attributes': {'name': name}}


def archive_bytes(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return data.getvalue()


class RequirementAttachmentTests(unittest.TestCase):
    def setUp(self):
        # Keep application settings unfrozen until the workflow fixtures initialize.
        from app.services.tfs import TfsClient
        from app.services.requirement_attachments import context, prepare_file
        self.client_type = TfsClient
        self.context = context
        self.prepare_file = prepare_file

    def test_all_relations_are_collected_without_a_description_keyword_or_image_cap(self):
        item = {'description': '实现新功能', 'relations': [attachment(f'screen-{i}.png', str(i)) for i in range(35)]}
        item['relations'].append(attachment('requirements.docx', 'document'))
        self.assertEqual(len(self.client_type._requirement_attachment_sources(item)), 36)
        self.assertEqual(len(self.client_type._requirement_image_sources(item)), 35)
        item['description'] = '<img src="'+item['relations'][0]['url']+'">'
        self.assertEqual(len(self.client_type._requirement_attachment_sources(item)), 36)

    def test_documents_are_downloaded_with_auth_and_included_in_both_task_entry_points(self):
        from app.services.codex_runner import CodexRunner
        item = {'id': 111, 'revision': 3, 'description': '实现需求',
                'relations': [attachment('../../需求说明.txt'), attachment('空文件.txt', 'empty')]}
        requests = []

        def serve(request):
            requests.append(request)
            content = '附件中独有的业务条件'.encode() if request.url.path.endswith('/one') else b''
            return httpx.Response(200, content=content, headers={'content-type': 'text/plain'})

        transport = httpx.MockTransport(serve)
        with tempfile.TemporaryDirectory() as directory, \
                patch('app.services.codex_runner.settings', SimpleNamespace(data_dir=Path(directory))), \
                patch.object(self.client_type, '_client', side_effect=lambda: httpx.Client(transport=transport, auth=('', 'test-pat'))):
            events = []
            result = CodexRunner._requirement_attachment_context(item, {'tfs_collection_url': 'https://tfs.test/DefaultCollection'},
                                                                 lambda *args: events.append(args))
            self.assertIn('附件中独有的业务条件', result)
            self.assertIn('无论正文是否提到附件', result)
            self.assertIn('不能覆盖本次用户授权', result)
            manifest = next(Path(directory).rglob('attachment-index.json'))
            files = json.loads(manifest.read_text('utf-8'))['attachments']
            self.assertEqual(len(files), 1)
            self.assertTrue(Path(files[0]['path']).is_relative_to(Path(directory)))
            self.assertEqual(Path(files[0]['text_path']).read_text('utf-8'), '附件中独有的业务条件')
            self.assertEqual(events[0][0], 'tfs.attachments_prepared')
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.headers.get('authorization', '').startswith('Basic ') for request in requests))
        code = Path('app/services/codex_runner.py').read_text('utf-8')
        self.assertIn('requirement_image_context = self._requirement_attachment_context', code)
        self.assertEqual(code.count('{requirement_image_context}'), 2)

    def test_missing_or_empty_attachments_are_ignored(self):
        client = self.client_type('https://tfs.test/DefaultCollection', pat='test')
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)/'absent'
            self.assertEqual(self.context(client, {'relations': []}, destination, lambda *args: None), '')
            self.assertFalse(destination.exists())
            item = {'relations': [attachment('empty.txt')]}
            with patch.object(client, '_download_requirement_attachment', return_value=(b'', 'text/plain')):
                self.assertEqual(self.context(client, item, destination, lambda *args: None), '')
            self.assertFalse(destination.exists())

    def test_redirect_is_validated_before_sending_credentials_to_another_host(self):
        contacted = []

        def serve(request):
            contacted.append(request.url.host)
            return httpx.Response(302, headers={'location': 'https://external.test/attachment'})

        client = self.client_type('https://tfs.test/DefaultCollection', pat='test')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(client, '_client', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(serve), auth=('', 'test'))):
            result = client.download_requirement_attachments({'relations': [attachment('requirements.txt')]}, Path(directory))
        self.assertEqual(contacted, ['tfs.test'])
        self.assertEqual(result['files'], [])
        self.assertIn('当前 TFS 服务', result['errors'][0])

    def test_failed_download_does_not_hide_other_attachments(self):
        client = self.client_type('https://tfs.test/DefaultCollection', pat='test')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(client, '_download_requirement_attachment', side_effect=[httpx.ConnectError('offline'), (b'other requirement', 'text/plain')]):
            result = self.context(client, {'relations': [attachment('failed.txt'), attachment('valid.txt', 'two')]}, Path(directory), lambda *args: None)
        self.assertIn('failed.txt', result)
        self.assertIn('other requirement', result)

    def test_word_text_tables_and_embedded_images_are_prepared(self):
        document = '<w:document xmlns:w="urn:word"><w:body><w:p><w:r><w:t>需求正文</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格条件</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'spec.docx'
            path.write_bytes(archive_bytes({'word/document.xml': document, 'word/media/image1.png': b'image'}))
            result = self.prepare_file(path, Path(directory)/'contents')
            self.assertIn('需求正文', result['text'])
            self.assertIn('表格条件', result['text'])
            self.assertEqual(len(result['images']), 1)

    def test_spreadsheet_sheets_strings_cells_formulas_and_cached_values_are_read(self):
        entries = {
            'xl/workbook.xml': '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="验收表" r:id="r1"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
            'xl/sharedStrings.xml': '<sst><si><t>保存重开</t></si></sst>',
            'xl/worksheets/sheet1.xml': '<worksheet><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>已执行</t></is></c><c r="C1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'spec.xlsx'
            path.write_bytes(archive_bytes(entries))
            text = self.prepare_file(path, Path(directory)/'contents')['text']
        for value in ['验收表', 'A1 = 保存重开', 'B1 = 已执行', 'C1 = 2', 'formula 1+1']:
            self.assertIn(value, text)

    def test_pdf_text_and_original_are_available_for_diagram_inspection(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'spec.pdf'
            writer = PdfWriter()
            page = writer.add_blank_page(width=200, height=200)
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 12 Tf 10 100 Td (PDF requirement) Tj ET')
            page[NameObject('/Contents')] = stream
            writer.write(path)
            result = self.prepare_file(path, Path(directory)/'contents')
            self.assertIn('PDF requirement', result['text'])
            self.assertEqual(result['pending'], [str(path)])

    def test_archive_paths_are_flattened_and_legacy_formats_remain_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'spec.zip'
            path.write_bytes(archive_bytes({'../../outside.txt': 'zip requirement', 'legacy.doc': b'\xd0\xcf\x00binary'}))
            result = self.prepare_file(path, Path(directory)/'contents')
            self.assertIn('zip requirement', result['text'])
            self.assertEqual(len(result['pending']), 1)
            self.assertTrue(Path(result['pending'][0]).is_relative_to(Path(directory)))
            self.assertFalse((Path(directory)/'outside.txt').exists())

    def test_gb18030_and_utf16_text_are_decoded_without_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'spec.txt'
            for encoding in ['gb18030', 'utf-16', 'utf-8-sig']:
                with self.subTest(encoding=encoding):
                    path.write_bytes('中文业务条件'.encode(encoding))
                    self.assertEqual(self.prepare_file(path, Path(directory)/'contents')['text'], '中文业务条件')
