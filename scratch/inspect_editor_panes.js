const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

for (let s of [2, 3]) {
  const m = content.match(new RegExp(`id=['"]slide-${s}['"][\\s\\S]*?<\\/section>`, 'i'));
  if (m) {
    console.log(`=== SLIDE ${s} ===`);
    const lines = m[0].split(/\r?\n/);
    lines.forEach((l, i) => {
      if (l.includes('pane-editor') || l.includes('toolbar') || l.includes('editor-viewport') || l.includes('contenteditable') || l.includes('tiptap') || l.includes('Extracted Text')) {
        console.log(`${i}: ${l}`);
      }
    });
  }
}
