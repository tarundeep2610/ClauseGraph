const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m = content.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);
const lines = m[0].split(/\r?\n/);
for (let i = 524; i <= 600; i++) {
  if (i < lines.length) console.log(`${i}: ${lines[i]}`);
}
