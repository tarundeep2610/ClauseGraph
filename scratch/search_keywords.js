const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const terms = ['smudge', 'decimal', 'skew', 'carbon', 'stamp', 'obscur', 'financial table', 'faded', 'signature'];
terms.forEach(t => {
  let idx = 0;
  while ((idx = content.toLowerCase().indexOf(t, idx)) !== -1) {
    console.log(`Found "${t}" at offset ${idx}:`);
    const start = Math.max(0, idx - 100);
    const end = Math.min(content.length, idx + 100);
    console.log(content.substring(start, end));
    console.log('---');
    idx += t.length;
  }
});
