package com.al1s.terminal.runtime

import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.junit.Assert.*
import org.junit.Test

class PlatformReachabilityTest {
    private class Socket(private val original:Request):WebSocket {
        var cancelled=false
        override fun request()=original
        override fun queueSize()=0L
        override fun send(text:String)=true
        override fun send(bytes:ByteString)=true
        override fun close(code:Int,reason:String?):Boolean {cancelled=true;return true}
        override fun cancel() {cancelled=true}
    }
    private class Harness {
        var identity:ReachabilityIdentity?=ReachabilityIdentity("https://platform","terminal","test-only")
        val sockets=mutableListOf<Socket>()
        val callbacks=mutableListOf<WebSocketListener>()
        var opened=0
        val agent=PlatformReachability({identity},{request,listener->
            callbacks.add(listener);Socket(request).also {sockets.add(it)}
        },{opened++})
    }
    @Test fun usesOneCredentialAuthenticatedConnectionWithoutOriginOrSecretInUrl() {
        val h=Harness();h.agent.reconcile();h.agent.reconcile()
        assertEquals(1,h.sockets.size)
        val request=h.sockets.single().request()
        // OkHttp stores a WSS request as HTTPS before its WebSocket handshake upgrade.
        assertEquals("https://platform/api/v1/terminal/reachability",request.url.toString())
        assertEquals("Bearer test-only",request.header("Authorization"));assertNull(request.header("Origin"))
    }
    @Test fun changingIdentityCancelsOldSocketAndLateFailureCannotDiscardNewOne() {
        val h=Harness();h.agent.reconcile()
        h.identity=ReachabilityIdentity("https://other","terminal","new-test-only");h.agent.reconcile()
        assertTrue(h.sockets[0].cancelled)
        h.callbacks[0].onFailure(h.sockets[0],java.io.IOException(),null)
        h.agent.reconcile();assertEquals(2,h.sockets.size)
    }
    @Test fun failedCurrentSocketCanRetryButStopAndMissingIdentityCloseIt() {
        val h=Harness();h.agent.reconcile()
        h.callbacks[0].onFailure(h.sockets[0],java.io.IOException(),null);h.agent.reconcile()
        assertEquals(2,h.sockets.size)
        h.identity=null;h.agent.reconcile();assertTrue(h.sockets[1].cancelled)
        h.agent.close();h.identity=ReachabilityIdentity("https://platform","terminal","test-only")
        h.agent.reconcile();assertEquals(2,h.sockets.size)
    }
    @Test fun onlyAValidCurrentOpenKicksHeartbeatAndPulseDoesNotOperateDevice() {
        val h=Harness();h.agent.reconcile()
        val response=Response.Builder().request(h.sockets[0].request()).protocol(okhttp3.Protocol.HTTP_1_1)
            .code(101).message("Switching Protocols").build()
        h.callbacks[0].onOpen(h.sockets[0],response)
        h.callbacks[0].onMessage(h.sockets[0],"al1s-reachability-v1")
        assertEquals(1,h.opened)
        h.agent.close();h.callbacks[0].onOpen(h.sockets[0],response)
        assertEquals(1,h.opened)
    }
    @Test fun anUnexpectedPayloadClosesTheChannelAndAllowsAHandshakeRetry() {
        val h=Harness();h.agent.reconcile()
        h.callbacks[0].onMessage(h.sockets[0],"click device")
        assertTrue(h.sockets[0].cancelled);assertEquals(0,h.opened)
        h.agent.reconcile();assertEquals(2,h.sockets.size)
    }
    @Test fun aNewDefaultNetworkReplacesEvenAnApparentlyOpenSocket() {
        val h=Harness();h.agent.reconcile();h.agent.reconcile(networkChanged=true)
        assertTrue(h.sockets[0].cancelled);assertEquals(2,h.sockets.size)
        h.callbacks[0].onClosed(h.sockets[0],1000,"")
        h.agent.reconcile();assertEquals(2,h.sockets.size)
    }
}
