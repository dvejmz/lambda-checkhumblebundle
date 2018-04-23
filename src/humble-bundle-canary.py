from __future__ import print_function
from lxml import html
import datetime
import json
import tempfile
import string
import re
import boto3
import traceback
import logging
import os
import requests

sns_client = boto3.client('sns')
s3_client = boto3.client('s3')

logger = logging.getLogger('humble-bundle-canary')
logger.setLevel(logging.DEBUG)

MAX_SMS_LENGTH = 160
S3_BUCKET = os.environ.get('S3_BUCKET') or 'humble-bundle-canary'
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
BASE_URL = 'https://www.humblebundle.com'

def get_latest_file(files):
    timestamps = []
    for f in files:
        timestamps.append(f['LastModified'])

    # If the number of keys in the bucket was non-zero but we didn't get anything
    # in, something odd must have happened.
    if len(timestamps) == 0:
        logger.warn('No files found')
        return None

    timestamps.sort(reverse=True)
    latest_file_key = [ f['Key'] for f in files if f['LastModified'] == timestamps[0] ][0]

    if latest_file_key is None: return None
    return s3_client.get_object(Bucket=S3_BUCKET, Key=latest_file_key)

def get_files():
    try:
        ls_objs_response = s3_client.list_objects_v2(Bucket=S3_BUCKET)
    except Exception as e:
        logger.error('Failed to access S3 bucket {}: {}'.format(S3_BUCKET, e.message))
        return []
    
    # Check if there are any files in the bucket
    if ls_objs_response['KeyCount'] == 0:
        logger.info('Bucket is empty')
        return []
    
    return ls_objs_response['Contents']

def save_topics(topics):
    if topics is None: return False

    logger.info("Uploading today's topics to bucket...")
    filename = datetime.datetime.now().strftime('%Y-%m-%d') + ".json"

    try:
        with tempfile.TemporaryFile('r+b') as data:
            json.dump(topics, data)
            data.seek(0, 0)
            s3_client.upload_fileobj(data, S3_BUCKET, filename)
    except Exception as e:
        logger.error('Failed to save topics in bucket "{}": '.format(S3_BUCKET, e.message))
        logger.debug(traceback.format_exc())
        return False

    logger.info('Created topics JSON file ' + filename + ' in bucket ' + S3_BUCKET)
    return True

def check_new_topics(current_topics):
    logger.info('Checking if current bundle is new...')
    prev_topics = get_files()

    if len(prev_topics) == 0: return True
    latest_topics = get_latest_file(prev_topics)

    if latest_topics is None: raise Exception('Files found but error retrieving latest one')
    latest_topics_json = latest_topics['Body'].read()

    # Check if previous topics contents is the same as the current one
    quotes_regex = r'[\'"]'
    return re.sub(quotes_regex, '', json.dumps(current_topics)) != re.sub(quotes_regex, '', latest_topics_json)

def scrape_html(html_tree):
    if html_tree is None:
        logger.error('HTML tree is empty')
        return []

    return html_tree.xpath('//*[@class="bundle-info-heading"]/text()')

def scrape_url(url, follow=False):
    if not url: raise Exception('No URL provided')

    try:
        page = requests.get(url)
    except Exception as e:
        logger.error('Failed to fetch URL {}: '.format(url, e.message))
        return []

    html_tree = html.fromstring(page.content)
    topics = []
    if follow:
        # Follow topic URLs in current URL and return them too
        topic_urls = map(lambda url: BASE_URL + url, find_topic_urls(html_tree))
        # Get the topics from the topic URLs in this page
        for url in topic_urls:
            topics.extend(scrape_url(url, False))
        # Get the topics of the current active page too, which will not be revisited above
        topics.extend(scrape_html(html_tree))
    else:
        topics = scrape_html(html_tree)

    return topics

def find_topic_urls(html_tree):
    if html_tree is None:
        logger.error('HTML tree is empty')
        return []

    return html_tree.xpath('//div[@id="subtab-container"]/a[not(@id="active-subtab")]/@href')

def send_notification(message):
    logger.info('Sending notification...')
    if SNS_TOPIC_ARN is None: raise Exception('No SNS ARN was found')
    try:
        response = sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message
        )
    except Exception as e:
        raise Exception('Failed to publish SNS topic: {}. ARN: {}'.format(e.message, SNS_TOPIC_ARN))

    response_code = response.get('ResponseMetadata').get('HTTPStatusCode')
    logger.info('Notification send reponse received with status ' + str(response_code))
    return response_code is not None and response_code == 200

def format_topic(topic_html):
    topic = re.sub(r' presented by [\w\s]+$', '', topic_html.strip(' \n'))
    topic = re.sub(r'The Humble [\w\s]+ Bundle: ', '', topic)
    # Ellipsize exceedingly long topics
    if len(topic) > 30:
        topic = topic[0:27] + "..."
    return "'" + topic + "'"

def get_todays_topics():
    logger.info("Retrieving today's bundle...")
    topics_urls = {
        'games': 'https://www.humblebundle.com',
        'books': 'https://www.humblebundle.com/books'
    }

    topics = {}
    for category, start_url in topics_urls.iteritems():
        topics[category] = map(format_topic, scrape_url(start_url, True))

    return topics

def lambda_handler(event, context):
    topics = get_todays_topics()

    try:
        topics_new = check_new_topics(topics)
    except Exception as e:
        logger.error("Failed to check topics recency status: " + e.message)
        logger.debug(traceback.format_exc())
        return False

    if not topics_new:
        logger.info('Notification unsent: bundles unchanged.')
        return True

    logger.info("New bundles found. Creating notification...")
    notification = ''
    for category, items in topics.iteritems():
        notification += category.upper() + ': ' + ', '.join(items) + '. '

    notification_len = len(notification)
    logger.info('Notification: {} Length: {}'.format(notification, notification_len))
    notification_sent = False

    if (notification_len > 0 and notification_len <= MAX_SMS_LENGTH):
        try:
            notification_sent = send_notification(notification)
        except Exception as e:
            logger.error('Failed to send notification: ' + e.message)
            logger.debug(traceback.format_exc())
            return False
    else:
        logger.error('Notification unsent: notification body empty.')
        return False

    try:
        json_topics = {}
        for category, items in topics.iteritems():
            json_topics[category] = map(lambda t: t.strip("'"), items)
        topics_saved = save_topics(json_topics)
    except Exception as e:
        logger.error("Failed to save today's topics in bucket: " + e.message)
        logger.debug(traceback.format_exc())
        return False

    return notification_sent and topics_saved
