const fs = require('fs');

const filePath = 'OCR Studio \u2014 Streamlined UX Redesign Mockups.html';
let content = fs.readFileSync(filePath, 'utf8');

// 1. Slide 1 updates:
content = content.replace(
  'Recent Ingested Tenders',
  'Uploaded PDFs'
);
content = content.replace(
  'Supports scanned PDFs, multi-page tenders, blueprints &amp; carbon copies',
  'Supports scanned PDFs, multi-page documents, forms &amp; records'
);

console.log('Slide 1 strings replaced.');

// Let's verify Slide 1 replacement:
if (content.includes('Uploaded PDFs')) {
  console.log('Verified: Uploaded PDFs exists in content.');
} else {
  console.error('Failed to replace Recent Ingested Tenders!');
  process.exit(1);
}

fs.writeFileSync(filePath, content, 'utf8');
console.log('Slide 1 written successfully.');
