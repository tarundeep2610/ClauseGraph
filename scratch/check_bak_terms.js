const fs = require('fs');
const bak = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html.bak', 'utf8');

['Dense', 'Smudged', 'Skewed', 'Official Stamp'].forEach(term => {
  let idx = 0;
  while ((idx = bak.indexOf(term, idx)) !== -1) {
    console.log(`Bak match for "${term}" at ${idx}:`);
    console.log(bak.substring(Math.max(0, idx - 100), Math.min(bak.length, idx + 100)));
    idx += term.length;
  }
});
