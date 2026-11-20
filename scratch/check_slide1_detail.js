const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m1 = content.match(/id=['"]slide-1['"][\s\S]*?<\/section>/i);
if (m1) {
  const lines = m1[0].split(/\r?\n/);
  console.log('Slide 1 lines 480 to 540:');
  for (let i = 480; i < Math.min(lines.length, 545); i++) {
    console.log(`${i}: ${lines[i]}`);
  }
}
