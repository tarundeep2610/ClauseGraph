const fs = require('fs');
const content = fs.readFileSync('OCR Studio \u2014 Streamlined UX Redesign Mockups.html', 'utf8');

const s3Start = content.indexOf('id="slide-3"');
const s3End = content.indexOf('</section>', s3Start);
const s3 = content.substring(s3Start, s3End);

let idx = 0;
while ((idx = s3.indexOf('Original Scan', idx)) !== -1) {
  console.log('Slide 3 has "Original Scan" at relative pos', idx);
  console.log(s3.substring(idx - 50, idx + 200));
  idx += 13;
}
