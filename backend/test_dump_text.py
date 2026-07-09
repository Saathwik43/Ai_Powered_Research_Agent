import json
import fitz

with open('2209.11154v3.pdf', 'rb') as f:
    pdf_bytes = f.read()
    
doc = fitz.open(stream=pdf_bytes, filetype="pdf")
text = ""
for page in doc:
    text += page.get_text() + "\n"

with open('full_text.txt', 'w', encoding='utf-8') as f:
    f.write(text)
