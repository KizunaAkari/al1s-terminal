import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.al1s.terminal"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.al1s.terminal"
        minSdk = 26
        targetSdk = 36
        versionCode = 3
        versionName = "0.2.1-wake-recovery"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk { abiFilters += "arm64-v8a" }
        externalNativeBuild { cmake { arguments += "-DANDROID_STL=c++_static" } }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }
    externalNativeBuild { cmake { path = file("src/main/native/CMakeLists.txt"); version = "3.22.1" } }
    ndkVersion = "27.2.12479018"
    // app_process and Maa's plugin loader require actual files at nativeLibraryDir.
    packaging { jniLibs {
        useLegacyPackaging = true
        // Preserve the verified upstream ELF files. Re-stripping prebuilt OpenCV
        // rewrites its dynamic section and makes Android's linker reject it.
        keepDebugSymbols += "**/*.so"
    } }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.github.MuntashirAkon:libadb-android:3.1.1")
    implementation("org.conscrypt:conscrypt-android:2.5.3")
    implementation("org.bouncycastle:bcpkix-jdk15to18:1.81")
    implementation("androidx.room:room-runtime:2.8.4")
    ksp("androidx.room:room-compiler:2.8.4")
    implementation("androidx.work:work-runtime-ktx:2.11.2")
    testImplementation("org.json:json:20250517")
    testImplementation("junit:junit:4.13.2")
}

ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
}
