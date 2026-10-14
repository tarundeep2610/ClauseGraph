const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const m2 = content.match(/id=['"]slide-2['"][\s\S]*?<\/section>/i);
const m3 = content.match(/id=['"]slide-3['"][\s\S]*?<\/section>/i);

function getHeader(m, label) {
  const lines = m[0].split(/\r?\n/);
  console.log(`=== ${label} HEADER ===`);
  let inHeader = false;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('&lt;header')) inHeader = true;
    if (inHeader) console.log(`${i}: ${lines[i]}`);
    if (lines[i].includes('&lt;/header&gt;')) break;
  }
}

getHeader(m2, 'SLIDE 2');
getHeader(m3, 'SLIDE 3');
