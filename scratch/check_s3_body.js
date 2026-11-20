const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m3 = content.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);
if (m3) {
  const lines = m3[0].split(/\r?\n/);
  console.log('Slide 3 lines around <body>:');
  for (let i = 475; i < 540; i++) {
    console.log(`${i}: ${lines[i]}`);
  }
}
