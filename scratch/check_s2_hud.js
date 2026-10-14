const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=["']slide-2["'][\s\S]*?<\/section>/i);
const lines = m2[0].split(/\r?\n/);
for (let i = 660; i < Math.min(lines.length, 695); i++) {
  console.log(`${i}: ${lines[i]}`);
}
