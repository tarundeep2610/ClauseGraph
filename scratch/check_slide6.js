const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m = content.match(/id=["']slide-6["'][\s\S]*?<\/section>/i);
if (m) {
  console.log('Slide 6 found, length:', m[0].length);
  const lines = m[0].split(/\r?\n/);
  console.log('Slide 6 title/desc:');
  for (let i = 0; i < 25; i++) {
    console.log(lines[i]);
  }
}
