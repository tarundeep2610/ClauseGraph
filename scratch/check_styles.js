const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=["']slide-2["'][\s\S]*?<\/section>/i);
const m3 = content.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);

const s2Style = m2[0].match(/&lt;style&gt;([\s\S]*?)&lt;\/style&gt;/i);
const s3Style = m3[0].match(/&lt;style&gt;([\s\S]*?)&lt;\/style&gt;/i);

console.log('Slide 2 style length:', s2Style ? s2Style[1].length : 0);
console.log('Slide 3 style length:', s3Style ? s3Style[1].length : 0);
