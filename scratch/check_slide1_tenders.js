const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

// Check Slide 1 for "tenders"
const m1 = content.match(/id=['"]slide-1['"][\s\S]*?<\/section>/i);
if (m1) {
  const lines = m1[0].split(/\r?\n/);
  lines.forEach((l, i) => {
    if (l.toLowerCase().includes('tender') || l.toLowerCase().includes('ingest')) {
      console.log(`Slide 1 Line ${i}: ${l}`);
    }
  });
}
