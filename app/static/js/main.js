/**
 * API Test Suite for House-App
 * Base URL: https://www.house-app.casa/api/
 * Version: 1.0.0
 */

const API_BASE_URL = 'https://www.house-app.casa/api';
const TEST_TIMEOUT = 10000; // 10 seconds

// ==================== TEST HELPERS ====================
class ApiTester {
  constructor() {
    this.testResults = {
      passed: 0,
      failed: 0,
      warnings: 0,
      duration: 0
    };
    this.startTime = Date.now();
  }

  async makeRequest(method, endpoint, data = null, headers = {}) {
    const url = `${API_BASE_URL}${endpoint}`;
    const config = {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...headers
      },
      timeout: TEST_TIMEOUT
    };

    if (data) {
      config.body = JSON.stringify(data);
    }

    try {
      const response = await fetch(url, config);
      const responseData = await response.json();
      
      return {
        status: response.status,
        data: responseData,
        headers: response.headers
      };
    } catch (error) {
      return {
        error: true,
        message: error.message
      };
    }
  }

  async runTest(testName, testFunction) {
    try {
      console.log(`▶ Starting test: ${testName}`);
      await testFunction();
      this.testResults.passed++;
      console.log(`✓ PASSED: ${testName}`);
    } catch (error) {
      this.testResults.failed++;
      console.error(`✗ FAILED: ${testName}`, error.message);
    }
  }

  getResults() {
    this.testResults.duration = (Date.now() - this.startTime) / 1000;
    return this.testResults;
  }
}

// ==================== TEST CASES ====================
async function runApiTests() {
  const tester = new ApiTester();

  // 1. Authentication Tests
  await tester.runTest('POST /auth/login - Valid credentials', async () => {
    const response = await tester.makeRequest('POST', '/auth/login', {
      username: 'testuser',
      password: 'testpass123'
    });

    if (response.error) throw new Error('Request failed');
    if (response.status !== 200) throw new Error(`Expected 200, got ${response.status}`);
    if (!response.data.token) throw new Error('Missing auth token');
  });

  await tester.runTest('POST /auth/login - Invalid credentials', async () => {
    const response = await tester.makeRequest('POST', '/auth/login', {
      username: 'invalid',
      password: 'wrong'
    });

    if (response.error) throw new Error('Request failed');
    if (response.status !== 401) throw new Error(`Expected 401, got ${response.status}`);
  });

  // 2. Property Tests
  await tester.runTest('GET /properties - Unauthenticated access', async () => {
    const response = await tester.makeRequest('GET', '/properties');

    if (response.error) throw new Error('Request failed');
    if (response.status !== 401) throw new Error(`Expected 401, got ${response.status}`);
  });

  await tester.runTest('GET /properties - Authenticated access', async () => {
    // First login to get token
    const login = await tester.makeRequest('POST', '/auth/login', {
      username: 'testuser',
      password: 'testpass123'
    });

    if (login.error || !login.data.token) {
      throw new Error('Login failed - cannot proceed with test');
    }

    const response = await tester.makeRequest('GET', '/properties', null, {
      'Authorization': `Bearer ${login.data.token}`
    });

    if (response.error) throw new Error('Request failed');
    if (response.status !== 200) throw new Error(`Expected 200, got ${response.status}`);
    if (!Array.isArray(response.data)) throw new Error('Expected array of properties');
  });

  // 3. User Profile Tests
  await tester.runTest('GET /user/profile - Valid token', async () => {
    const login = await tester.makeRequest('POST', '/auth/login', {
      username: 'testuser',
      password: 'testpass123'
    });

    const response = await tester.makeRequest('GET', '/user/profile', null, {
      'Authorization': `Bearer ${login.data.token}`
    });

    if (response.error) throw new Error('Request failed');
    if (response.status !== 200) throw new Error(`Expected 200, got ${response.status}`);
    if (!response.data.id || !response.data.username) {
      throw new Error('Missing required user fields');
    }
  });

  // 4. Error Handling Tests
  await tester.runTest('GET /nonexistent - 404 Not Found', async () => {
    const response = await tester.makeRequest('GET', '/nonexistent');

    if (response.error) throw new Error('Request failed');
    if (response.status !== 404) throw new Error(`Expected 404, got ${response.status}`);
  });

  await tester.runTest('POST /properties - Unauthorized creation', async () => {
    const response = await tester.makeRequest('POST', '/properties', {
      title: 'Test Property',
      price: 250000
    });

    if (response.error) throw new Error('Request failed');
    if (response.status !== 401) throw new Error(`Expected 401, got ${response.status}`);
  });

  // 5. Performance Tests
  await tester.runTest('GET /properties - Response time < 1s', async () => {
    const login = await tester.makeRequest('POST', '/auth/login', {
      username: 'testuser',
      password: 'testpass123'
    });

    const start = Date.now();
    await tester.makeRequest('GET', '/properties', null, {
      'Authorization': `Bearer ${login.data.token}`
    });
    const duration = Date.now() - start;

    if (duration > 1000) {
      throw new Error(`Response took ${duration}ms (expected < 1000ms)`);
    }
  });

  // ==================== TEST SUMMARY ====================
  const results = tester.getResults();
  console.log('\n============ TEST SUMMARY ============');
  console.log(`Total Tests: ${results.passed + results.failed}`);
  console.log(`Passed: ${results.passed}`);
  console.log(`Failed: ${results.failed}`);
  console.log(`Duration: ${results.duration.toFixed(2)}s`);
  console.log('====================================\n');

  return results;
}

// ==================== RUN TESTS ====================
if (typeof module !== 'undefined' && module.exports) {
  // Node.js export for CI/CD testing
  module.exports = { runApiTests };
} else {
  // Browser execution
  document.addEventListener('DOMContentLoaded', () => {
    console.log('Starting API tests...');
    runApiTests().then(results => {
      if (results.failed > 0) {
        console.error('❌ Some tests failed');
      } else {
        console.log('✅ All tests passed');
      }
    });
  });
}