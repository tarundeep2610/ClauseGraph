const fs = require('fs');

const current = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');
const bak = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html.bak', 'utf8');

console.log('Current length:', current.length);
console.log('Bak length:', bak.length);

const mCurrent = current.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);
const mBak = bak.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);

if (mBak) {
  console.log('Bak has slide-3. Let us see queue items in bak:');
  const lines = mBak[0].split(/\r?\n/);
  lines.forEach((l, i) => {
    if (l.includes('Page 14') || l.includes('Page 22') || l.includes('Page 39') || l.includes('Dense') || l.includes('Skewed') || l.includes('Official')) {
      console.log(`Bak ${i}: ${l}`);
    }
  });
}
