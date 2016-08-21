"""
deoplete.vim completion for Dart using analysis_server
"""

import json
import os.path
import subprocess
import threading

from .base import Base
from deoplete.util import charpos2bytepos, error


class Source(Base):
    """
    Required class name by deoplete
    """

    def __init__(self, vim):
        Base.__init__(self, vim)

        self.name = 'dart'
        self.mark = '[Dart]'
        self.min_pattern_length = 1
        self.rank = 500
        self.filetypes = ['dart']
        self.use_on_event = 1
        self._server = None

    def on_init(self, context):
        """
        Set up analysis server
        """
        self.use_on_event = context['vars'].get(
            'deoplete#sources#dart#use_on_event', 1)

        #Dart paths
        dart_sdk_path = context['vars'].get(
            'deoplete#sources#dart#dart_sdk_path', '')
        dart_bin_dir = os.path.join(dart_sdk_path, 'bin')
        dart_bin = os.path.join(dart_bin_dir, 'dart')
        dart_analysis_server = os.path.join(
            dart_bin_dir, 'snapshots', 'analysis_server.dart.snapshot')

        flags_string = context['vars'].get(
            'deoplete#sources#dart#dart_analysis_server_flags', ''
        )

        self._server = AnalysisService(
            dart_bin, dart_analysis_server, flags_string + 
            ' --sdk ' + dart_sdk_path + ' --no-error-notification')

    def gather_candidates(self, context):
        """
        Request completions from analysis_server backend
        """
        current_file = os.path.join(context['cwd'], context['bufname'])
        #self._server.add_analysis_roots([current_file])

        line = self.vim.current.window.cursor[0]
        column = context['complete_position']
        offset = self.vim.call('line2byte', line) + \
            charpos2bytepos('utf-8', context['input'][: column],
                            column) - 1
        suggestions = self._server.get_suggestions(
            current_file, offset)

        candidates = []
        for suggest in suggestions:
            doc_summary = ''
            if 'docSummary' in suggest:
                doc_summary = suggest['docSummary']

            candidate = dict(word=suggest['completion'],
                             kind=suggest['kind'],
                             info=doc_summary,
                             dup=1)

            candidates.append(candidate)

        return candidates

    def on_event(self, context):
        """
        Make sure files are in the analyzer
        """
        current_file = os.path.join(context['cwd'], context['bufname'])
        if self.use_on_event == 1:
            self._server.add_analysis_roots([current_file])
        return


class AnalysisService(object):
    """
    A long-running process that provides analysis results to other tools.
    """

    def __init__(self, dart_bin, analysis_server_path, flags_string):
        flags = [] if not flags_string else flags_string.split(' ')
        cmd = [dart_bin, analysis_server_path] + flags
        self._request_id = 0
        self._lock = threading.RLock()
        self._roots = []
        self._priority_files = []
        self._process = subprocess.Popen(cmd,
                                         stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         universal_newlines=True)

        while True:
            line = self._process.stdout.readline()
            response = json.loads(line)
            if ('event' in response) and (response['event'] == 'server.connected'):
                return
            if 'error' in response:
                raise Exception(response['error'])

    def kill(self):
        """
        Shutdown analysis service
        """
        self._process.kill()

    def __get_next_request_id(self):
        self._request_id += 1
        return str(self._request_id)

    def __send_request_wait(self, method, params, result_type):
        with self._lock:
            response = self.__send_request(method, params)
            result_id = response['id']
            results = []
            while True:
                line = self._process.stdout.readline()
                response = json.loads(line)
                if (('event' in response)
                        and (response['event'] == result_type)
                        and (response['params']['id'] == result_id)):
                    params = response['params']
                    results.extend(params['results'])
                    if params['isLast']:
                        return results

    def __send_request(self, method, params):
        with self._lock:
            request_id = self.__get_next_request_id()
            request = {'id': request_id, 'method': method, 'params': params}
            json_req = json.dumps(request) + '\n'
            self._process.stdin.write(json_req)
            self._process.stdin.flush()
            while True:
                line = self._process.stdout.readline()
                response = json.loads(line)
                if ('id' in response) and (response['id'] == request_id):
                    if 'error' in response:
                        raise Exception(response['error'])
                    elif 'result' in response:
                        return response['result']
                    else:
                        return None

    def add_analysis_roots(self, filenames):
        """
        Add files to be analyzed
        """
        for filename in filenames:
            directory = os.path.dirname(filename)
            while (not os.path.exists(os.path.join(directory, 'pubspec.yaml'))
                   and directory != '' and directory != '/'):
                directory = os.path.dirname(directory)

            if directory == '' or directory == '/':
                directory = os.path.dirname(filename)

            if directory not in self._roots:
                self._roots.append(directory)

            if filename not in self._priority_files:
                self._priority_files.append(filename)

        self.set_analysis_roots(self._roots, [], {})
        self.set_priority_files(self._priority_files)

    def set_analysis_roots(self, included, excluded, package_roots):
        """
        Sets the root paths used to determine which files to analyze. The set
        of files to be analyzed are all of the files in one of the root paths
        that are not either explicitly or implicitly excluded. A file is
        explicitly excluded if it is in one of the excluded paths. A file is
        implicitly excluded if it is in a subdirectory of one of the root paths
        where the name of the subdirectory starts with a period (that is, a
        hidden directory).
        """
        return self.__send_request(
            'analysis.setAnalysisRoots',
            {
                'included': included,
                'excluded': excluded,
                'packageRoots': package_roots
            })

    def set_priority_files(self, files):
        """
        Set the priority files to the files in the given list. A priority file
        is a file that is given priority when scheduling which analysis work to
        do first. The list typically contains those files that are visible to
        the user and those for which analysis results will have the biggest
        impact on the user experience. The order of the files within the list
        is significant: the first file will be given higher priority than the
        second, the second higher priority than the third, and so on.
        """
        return self.__send_request(
            'analysis.setPriorityFiles',
            {
                'files': files
            })

    def update_file_content(self, filename, content):
        """
        Update the content of one or more files. Files that were previously
        updated but not included in this update remain unchanged. This
        effectively represents an overlay of the filesystem. The files whose
        content is overridden are therefore seen by server as being files with
        the given content, even if the files do not exist on the filesystem or
        if the file path represents the path to a directory on the filesystem.
        """
        return self.__send_request(
            'analysis.updateContent',
            {
                'files': {
                    filename: {'type': 'add', 'content': content}
                }
            })

    def get_errors(self, filename):
        """
        Return the errors associated with the given file. If the errors for the
        given file have not yet been computed, or the most recently computed
        errors for the given file are out of date, then the response for this
        request will be delayed until they have been computed. If some or all
        of the errors for the file cannot be computed, then the subset of the
        errors that can be computed will be returned and the response will
        contain an error to indicate why the errors could not be computed. If
        the content of the file changes after this request was received but
        before a response could be sent, then an error of type CONTENT_MODIFIED
        will be generated.
        """
        return self.__send_request(
            'analysis.getErrors',
            {
                'file': filename
            })

    def get_navigation(self, filename, offset, length):
        """
        Return the navigation information associated with the given region of
        the given file. If the navigation information for the given file has
        not yet been computed, or the most recently computed navigation
        information for the given file is out of date, then the response for
        this request will be delayed until it has been computed. If the content
        of the file changes after this request was received but before a
        response could be sent, then an error of type CONTENT_MODIFIED will be
        generated.
        """
        return self.__send_request(
            'analysis.getNavigation',
            {
                'file': filename,
                'offset': offset,
                'length': length
            })

    def get_hover(self, filename, offset):
        """
        Return the hover information associate with the given location. If some
        or all of the hover information is not available at the time this
        request is processed the information will be omitted from the response.
        """
        return self.__send_request(
            'analysis.getHover',
            {
                'file': filename,
                'offset': offset
            })

    def get_suggestions(self, filename, offset):
        """
        Request that completion suggestions for the given offset in the given
        file be returned.
        """
        return self.__send_request_wait(
            'completion.getSuggestions',
            {
                'file': filename,
                'offset': offset
            },
            'completion.results')
