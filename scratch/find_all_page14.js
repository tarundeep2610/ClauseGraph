const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

let pos = 0;
while ((pos = content.indexOf('Page 14', pos)) !== -1) {
  const start = Math.max(0, pos - 150);
  const end = Math.min(content.length, pos + 250);
  console.log(`Match at ${pos}:`);
  console.log(content.substring(start, end));
  console.log('='.repeat(50));
  pos += 7;
}
