const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m3 = content.match(/id=['"]slide-3['"][\s\S]*?<\/section>/i);
if (m3) {
  const lines = m3[0].split(/\r?\n/);
  console.log('Slide 3 lines 535 to 570:');
  for (let i = 535; i < Math.min(lines.length, 575); i++) {
    console.log(`${i}: ${lines[i]}`);
  }
}
