package com.al1s.terminal.activation

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.math.BigInteger
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.cert.Certificate
import java.security.cert.CertificateFactory
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Date
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.security.auth.x500.X500Principal
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder

class PairingIdentityStore(context: Context) {
    private val preferences = context.getSharedPreferences("local-adb-identity", Context.MODE_PRIVATE)
    private val alias = "al1s-local-adb-storage-v1"

    fun hasIdentity(): Boolean = preferences.contains("private") && preferences.contains("certificate")

    fun loadOrCreate(): Pair<PrivateKey, Certificate> = synchronized(identityLock) { loadIdentity() }

    private fun loadIdentity(): Pair<PrivateKey, Certificate> {
        val private = preferences.getString("private", null)
        val certificate = preferences.getString("certificate", null)
        if (private != null && certificate != null) return KeyFactory.getInstance("RSA")
            .generatePrivate(PKCS8EncodedKeySpec(decrypt(private))) to
            CertificateFactory.getInstance("X.509").generateCertificate(Base64.decode(certificate, Base64.NO_WRAP).inputStream())
        check(private == null && certificate == null) { "Pairing identity is incomplete" }
        val pair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        val subject = X500Principal("CN=AL1S Local Device")
        val holder = JcaX509v3CertificateBuilder(subject, BigInteger(128, SecureRandom()),
            Date(System.currentTimeMillis() - 60_000), Date(System.currentTimeMillis() + 10L * 365 * 86400 * 1000),
            subject, pair.public).build(JcaContentSignerBuilder("SHA256withRSA").build(pair.private))
        val cert = JcaX509CertificateConverter().getCertificate(holder)
        check(preferences.edit().putString("private", encrypt(pair.private.encoded))
            .putString("certificate", Base64.encodeToString(cert.encoded, Base64.NO_WRAP)).commit())
        return pair.private to cert
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    private fun encrypt(value: ByteArray): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        return Base64.encodeToString(cipher.iv + cipher.doFinal(value), Base64.NO_WRAP)
    }
    private fun decrypt(value: String): ByteArray {
        val bytes = Base64.decode(value, Base64.NO_WRAP)
        require(bytes.size > 28)
        return Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
        }.doFinal(bytes.copyOfRange(12, bytes.size))
    }

    companion object { private val identityLock = Any() }
}
