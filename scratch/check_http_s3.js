const http = require('http');

http.get('http://localhost:4891/', (res) => {
  let data = '';
  res.on('data', chunk => data += chunk);
  res.on('end', () => {
    console.log('HTTP response length:', data.length);
    const m3 = data.match(/id=["']slide-3["'][\s\S]*?<\/section>/i);
    if (m3) {
      console.log('In HTTP server slide-3:');
      console.log('Has Original Scan (Page 14)?', m3[0].includes('Original Scan (Page 14)'));
      console.log('Has Original Scan (Page 3)?', m3[0].includes('Original Scan (Page 3)'));
      console.log('Has Extracted Text (Page 14)?', m3[0].includes('Extracted Text (Page 14)'));
    }
  });
});
