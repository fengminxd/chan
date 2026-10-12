"""Cross-contract rendering must not leak the template's original asset."""
import tempfile
import unittest
from pathlib import Path
from report_sui import render


class ContractRenderingTests(unittest.TestCase):
    def test_contract_identity_and_filename_are_consistent(self):
        for symbol in ('SUIUSDT', 'NEARUSDT'):
            with self.subTest(symbol=symbol), tempfile.TemporaryDirectory() as td:
                folder=Path(td)
                payload=dict(symbol=symbol,start='2026-06-19T09:00:00+00:00',
                             asof='2026-09-27T09:00:00+00:00',histories={})
                render(folder,payload)
                name=symbol[:-4].lower()+'.html'
                page=(folder/name).read_text()
                self.assertIn(symbol+' · 1h / 4h',page)
                self.assertIn(symbol[:-4]+' 永续 · 1h / 4h',page)
                self.assertIn(']('+name+')',(folder/'result.md').read_text())
                if symbol=='NEARUSDT':
                    self.assertNotIn('SUI',page)
                    self.assertFalse((folder/'sui.html').exists())

    def test_invalid_contract_cannot_inject_html_or_output_paths(self):
        for symbol in ('../NEARUSDT','NEAR<script>USDT','NEAR/USDT'):
            with tempfile.TemporaryDirectory() as td:
                with self.assertRaises(ValueError):
                    render(Path(td),dict(symbol=symbol))


if __name__=='__main__':unittest.main()
