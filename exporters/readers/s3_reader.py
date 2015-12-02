import gzip
import json
import os
import tempfile
import re

from exporters.readers.base_reader import BaseReader
from exporters.records.base_record import BaseRecord
from exporters.default_retries import retry_long


class S3Reader(BaseReader):
    """
    Reads items from s3 keys with a common prefix.

        - batch_size (int)
            Number of items to be returned in each batch

        - bucket (str)
            Name of the bucket to retrieve items from.

        - aws_access_key_id (str)
            Public acces key to the s3 bucket.

        - aws_secret_access_key (str)
            Secret access key to the s3 bucket.

        - prefix (str)
            Prefix of s3 keys to be read.
    """

    # List of options to set up the reader
    supported_options = {
        'batch_size': {'type': int, 'default': 10000},
        'bucket': {'type': basestring},
        'aws_access_key_id': {'type': basestring, 'env_fallback': 'EXPORTERS_S3READER_AWS_KEY'},
        'aws_secret_access_key': {'type': basestring, 'env_fallback': 'EXPORTERS_S3READER_AWS_SECRET'},
        'prefix': {'type': basestring}
    }

    def __init__(self, options):
        import boto
        super(S3Reader, self).__init__(options)
        self.batch_size = self.read_option('batch_size')
        self.connection = boto.connect_s3(self.read_option('aws_access_key_id'), self.read_option('aws_secret_access_key'))
        self.bucket = self.connection.get_bucket(self.read_option('bucket'))
        self.prefix = self.read_option('prefix')
        self._chekv_valid_prefix()
        non_regex_prefix, regex_prefix = self.get_non_regex_prefix()
        self.keys = []
        for key in self.bucket.list(prefix=non_regex_prefix):
            if re.match(self.prefix, key.name):
                self.keys.append(key.key)
        self.read_keys = []
        self.current_key = None
        self.last_line = 0
        self.logger.info('S3Reader has been initiated')
        self.tmp_folder = tempfile.mkdtemp()

    def _chekv_valid_prefix(self):
        re.compile(self.prefix)

    def get_non_regex_prefix(self):
        prefix_parts = self.prefix.split('/')
        safe_parts = []
        for part in prefix_parts:
            compiled = re.compile(part)
            if compiled.groups > 0:
                break
            else:
                safe_parts.append(part)
        regex_parts = list(set(prefix_parts) - set(safe_parts))
        safe_prefix = '/'.join(safe_parts)
        regex_prefix = '/'.join(regex_parts)
        return safe_prefix, regex_prefix

    @retry_long
    def get_key(self, file_path):
        self.bucket.get_key(self.current_key).get_contents_to_filename(file_path)

    def get_next_batch(self):
        file_path = '{}/ds_dump.gz'.format(self.tmp_folder)
        if not self.current_key:
            self.current_key = self.keys[0]
            self.get_key(file_path)
            self.last_line = 0

        dump_file = gzip.open(file_path, 'r')
        lines = dump_file.readlines()
        if self.last_line+self.batch_size <= len(lines):
            last_item = self.last_line+self.batch_size
        else:
            last_item = len(lines)
            self.read_keys.append(self.current_key)
            self.keys.remove(self.current_key)
            self.current_key = None
            if len(self.keys) == 0:
                self.finished = True
                os.remove(file_path)
        for line in lines[self.last_line:last_item]:
            line = line.replace("\n", '')
            item = BaseRecord(json.loads(line))
            self.stats['read_items'] += 1
            yield item
        dump_file.close()

        self.last_line += self.batch_size

        self.last_position['keys'] = self.keys
        self.last_position['read_keys'] = self.read_keys
        self.last_position['current_key'] = self.current_key
        self.last_position['last_line'] = self.last_line

        self.logger.debug('Done reading batch')


    def set_last_position(self, last_position):
        if last_position is None:
            self.last_position = {}
            self.last_position['keys'] = self.keys
            self.last_position['read_keys'] = self.read_keys
            self.last_position['current_key'] = None
            self.last_position['last_line'] = 0
        else:
            self.last_position = last_position
            self.keys = self.last_position['keys']
            self.read_keys = self.last_position['read_keys']
            file_path = '{}/ds_dump.gz'.format(self.tmp_folder)
            if self.last_position['current_key']:
                self.current_key = self.last_position['current_key']
            else:
                self.current_key = self.keys[0]
                self.bucket.get_key(self.current_key).get_contents_to_filename(file_path)
                self.last_line = 0
            self.last_line = self.last_position['last_line']

