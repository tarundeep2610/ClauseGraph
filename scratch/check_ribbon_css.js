const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=['"]slide-2['"][\s\S]*?<\/section>/i);
if (m2) {
  const lines = m2[0].split(/\r?\n/);
  console.log('Slide 2 ribbon CSS:');
  lines.forEach((l, i) => {
    if (l.includes('editor-ribbon') || l.includes('ribbon-btn') || l.includes('ribbon-select') || l.includes('ribbon-group') || l.includes('ribbon-divider')) {
      console.log(`${i}: ${l}`);
    }
  });
}
