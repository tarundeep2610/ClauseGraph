const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m = content.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);
const lines = m[0].split(/\r?\n/);
for (let i = 495; i <= 535; i++) {
  console.log(`${i}: ${lines[i]}`);
}
