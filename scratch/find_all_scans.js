const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

let pos = 0;
while ((pos = content.indexOf('Original Scan', pos)) !== -1) {
  // Find which slide this is in
  const before = content.substring(0, pos);
  const slideMatch = before.match(/id=["'](slide-[0-9]+)["']/g);
  const currentSlide = slideMatch ? slideMatch[slideMatch.length - 1] : 'unknown';
  console.log(`Found "Original Scan" in ${currentSlide} at ${pos}:`);
  console.log(content.substring(pos, pos + 120));
  pos += 13;
}
