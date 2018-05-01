# Humble Bundle Canary (Legacy)

**The new single-page, JavasScript-rendered overhaul of humblebundle.com makes it impossible to scrape the website without making fundamental changes to the way this script works, such as installing PhantomJS & Selenium or more cumbersome alternatives like Xvfb, PyQt, etc. As such, I've decided to discontinue this project and write a new one from scratch using more modern, native tools like [Puppeteer](https://github.com/GoogleChrome/puppeteer).**

Humble Bundle Canary is a Lambda-optimised application that scrapes humblebundle.com on a weekly basis and extracts the latest bundles and offers to then send them via SMS to your phone. It saves every past bundle in a JSON file hosted in S3.
