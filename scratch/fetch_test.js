const http = require('http');

http.get('http://localhost:4891/', (res) => {
  let data = '';
  res.on('data', chunk => data += chunk);
  res.on('end', () => {
    console.log('Fetched length:', data.length);
    if (data.includes('Dense Financial Table')) {
      console.log('Found "Dense Financial Table" in HTTP response!');
    } else {
      console.log('"Dense Financial Table" NOT found in HTTP response.');
    }
  });
});
