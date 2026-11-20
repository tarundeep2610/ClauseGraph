const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

for (let s = 1; s <= 8; s++) {
  const m = content.match(new RegExp(`id=['"]slide-${s}['"][\\s\\S]*?<\\/section>`, 'i'));
  if (m) {
    const lines = m[0].split(/\r?\n/);
    lines.forEach((l, i) => {
      if (l.includes('Tender_') || l.includes('tender') || l.includes('Ingested')) {
        console.log(`Slide ${s} line ${i}: ${l}`);
      }
    });
  }
}
