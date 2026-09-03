import json
import tempfile
import unittest
from pathlib import Path

from ytloadlib import transcripts


VTT = '''WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:02.500 align:start
Hello <00:00:01.500><c>world</c>

00:00:02.000 --> 00:00:04.000
Hello world
Welcome &amp; enjoy.

00:00:08.000 --> 00:00:09.000
Hello world
'''


class TranscriptTests(unittest.TestCase):
    def test_rolling_captions_deduplicate_without_deleting_later_speech(self):
        cues = transcripts.parse_captions(VTT, 'vtt')
        self.assertEqual([c.text for c in cues], ['Hello world', 'Welcome & enjoy.', 'Hello world'])
        self.assertEqual(cues[0].start, 1.0)
        self.assertEqual(cues[1].end, 4.0)

    def test_json3_and_srt(self):
        data = json.dumps({'events': [{'tStartMs': 1500, 'dDurationMs': 2000, 'segs': [{'utf8': 'A & B'}]}, {'tStartMs': 3500}]})
        self.assertEqual(transcripts.parse_captions(data, 'json3')[0].text, 'A & B')
        cue = transcripts.parse_captions('1\n00:00:01,500 --> 00:00:03,500\nText\n', 'srt')[0]
        self.assertEqual((cue.start, cue.end, cue.text), (1.5, 3.5, 'Text'))

    def test_export_all_formats_and_keep_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'Example.en.vtt'
            source.write_text(VTT, encoding='utf-8')
            outputs = transcripts.export_transcript(source, ['txt', 'srt', 'vtt', 'json'], timestamps=True)
            self.assertEqual({p.suffix for p in outputs}, {'.txt', '.srt', '.vtt', '.json'})
            self.assertEqual(source.read_text(encoding='utf-8'), VTT)
            self.assertIn('[00:00:01]', source.with_suffix('.txt').read_text())
            self.assertIn('00:00:01,000 -->', source.with_suffix('.srt').read_text())
            self.assertEqual(json.loads(source.with_suffix('.json').read_text())['segments'][1]['text'], 'Welcome & enjoy.')

    def test_empty_or_unsupported_captions_fail_explicitly(self):
        for text, extension in [('WEBVTT\n', 'vtt'), ('bad', 'ass'), ('{}', 'json3')]:
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                transcripts.parse_captions(text, extension)

    def test_adjacent_authored_repeated_words_are_preserved(self):
        for extension in ('vtt', 'srt'):
            cues = transcripts.parse_captions('00:00:01.000 --> 00:00:02.000\nNo\n\n00:00:02.000 --> 00:00:03.000\nNo\n', extension)
            self.assertEqual([cue.text for cue in cues], ['No', 'No'])
