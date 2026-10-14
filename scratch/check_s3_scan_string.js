const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m3 = content.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);
if (m3) {
  console.log('Slide 3 has "Original Scan (Page 14)"?', m3[0].includes('Original Scan (Page 14)'));
  console.log('Slide 3 has "Original Scan (Page 3)"?', m3[0].includes('Original Scan (Page 3)'));
}
