const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=["']slide-2["'][\s\S]*?<\/section>/i);
if (m2) {
  const lines = m2[0].split(/\r?\n/);
  for (let i = 560; i < 625; i++) {
    console.log(`${i}: ${lines[i]}`);
  }
}
