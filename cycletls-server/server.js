const express = require('express');
const initCycleTLS = require('cycletls');
const app = express();
const port = 3000;

app.use(express.json()); // Middleware for parsing JSON bodies

app.post('/fetch', async (req, res) => {
  const { url, args, method = 'get' } = req.body; // Default method is 'get'

  try {
    console.log('Received request for URL:', url, 'with method:', method); // Log the requested URL and method

    const cycleTLS = await initCycleTLS();
    const response = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        console.log('Request timed out for URL:', url); // Log timeout
        cycleTLS.exit();
        reject(new Error('Request timed out'));
      }, 30000); // Timeout set to 30000 milliseconds (30 seconds)

      cycleTLS(url, args, method.toLowerCase())
        .then(response => {
          clearTimeout(timeout);
          resolve(response);
        })
        .catch(error => {
          clearTimeout(timeout);
          reject(error);
        });
    });

    cycleTLS.exit();
    res.send(response);
  } catch (error) {
    console.error('Error occurred:', error.message); // Log errors
    res.status(500).send({ error: error.message });
  }
});

app.listen(port, () => {
  console.log(`Server listening at http://localhost:${port}`); // Log server start
});
