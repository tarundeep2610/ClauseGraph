const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=['"]slide-2['"][\s\S]*?<\/section>/i);
if (m2) {
  const lines = m2[0].split(/\r?\n/);
  console.log('SLIDE 2 lines 600 to 640:');
  for (let i = 600; i < Math.min(lines.length, 640); i++) {
    console.log(`${i}: ${lines[i]}`);
  }
}
