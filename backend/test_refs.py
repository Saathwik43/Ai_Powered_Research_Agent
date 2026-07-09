import xml.etree.ElementTree as ET
from ai.grobid_client import _text_of

with open('fulltext.xml', encoding='utf-8') as f:
    text = f.read()
root = ET.fromstring(text)
ns = {'tei': 'http://www.tei-c.org/ns/1.0'}

refs_div = root.find('.//tei:back/tei:div[@type="references"]', ns)
if refs_div is not None:
    print('References text snippet:')
    print(_text_of(refs_div)[:500])

ack_div = root.find('.//tei:back/tei:div[@type="acknowledgement"]', ns)
if ack_div is not None:
    print('\nAck text snippet:')
    print(_text_of(ack_div)[:500])
