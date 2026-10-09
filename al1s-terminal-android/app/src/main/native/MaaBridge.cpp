#include <jni.h>
#include <dlfcn.h>
#include <string>
#include <mutex>
#include <MaaFramework/MaaAPI.h>

namespace {
std::mutex mutex;
void* framework = nullptr;
std::string text(JNIEnv* env, jstring value) {
    const char* raw = env->GetStringUTFChars(value, nullptr);
    std::string result(raw); env->ReleaseStringUTFChars(value, raw); return result;
}
template<class T> T api(const char* name) { return reinterpret_cast<T>(dlsym(framework, name)); }
void error(JNIEnv* env, const char* message) { env->ThrowNew(env->FindClass("java/lang/IllegalStateException"), message); }
}

void* al1sFrameworkApi(const char* name) { return framework ? dlsym(framework, name) : nullptr; }

extern "C" JNIEXPORT jstring JNICALL
Java_com_al1s_terminal_maa_NativeMaa_version(JNIEnv* env, jobject, jstring path) {
    std::lock_guard<std::mutex> lock(mutex);
    if (!framework) framework = dlopen(text(env,path).c_str(), RTLD_NOW | RTLD_GLOBAL);
    if (!framework) { error(env,"MaaFramework native load failed"); return nullptr; }
    auto version = api<decltype(&MaaVersion)>("MaaVersion");
    if (!version) { error(env,"MaaFramework version API missing"); return nullptr; }
    return env->NewStringUTF(version());
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_al1s_terminal_maa_NativeMaa_connect(JNIEnv* env, jobject, jstring, jstring config) {
    std::lock_guard<std::mutex> lock(mutex);
    auto create = api<decltype(&MaaAndroidNativeControllerCreate)>("MaaAndroidNativeControllerCreate");
    auto post = api<decltype(&MaaControllerPostConnection)>("MaaControllerPostConnection");
    auto wait = api<decltype(&MaaControllerWait)>("MaaControllerWait");
    auto destroy = api<decltype(&MaaControllerDestroy)>("MaaControllerDestroy");
    auto set = api<decltype(&MaaControllerSetOption)>("MaaControllerSetOption");
    if (!create || !post || !wait || !destroy || !set) { error(env,"Native controller API missing"); return 0; }
    MaaController* controller = create(text(env,config).c_str());
    bool raw = true;
    if (controller && !set(controller,MaaCtrlOption_ScreenshotUseRawSize,&raw,sizeof(raw))) {
        destroy(controller); error(env,"Original screenshot mode unavailable"); return 0;
    }
    if (!controller || wait(controller,post(controller)) != MaaStatus_Succeeded) {
        if(controller) destroy(controller); error(env,"Native controller connection failed"); return 0;
    }
    return reinterpret_cast<jlong>(controller);
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_com_al1s_terminal_maa_NativeMaa_screenshot(JNIEnv* env, jobject, jlong handle) {
    std::lock_guard<std::mutex> lock(mutex);
    auto controller = reinterpret_cast<MaaController*>(handle);
    auto post = api<decltype(&MaaControllerPostScreencap)>("MaaControllerPostScreencap");
    auto wait = api<decltype(&MaaControllerWait)>("MaaControllerWait");
    auto make = api<decltype(&MaaImageBufferCreate)>("MaaImageBufferCreate");
    auto cached = api<decltype(&MaaControllerCachedImage)>("MaaControllerCachedImage");
    auto encoded = api<decltype(&MaaImageBufferGetEncoded)>("MaaImageBufferGetEncoded");
    auto size = api<decltype(&MaaImageBufferGetEncodedSize)>("MaaImageBufferGetEncodedSize");
    auto destroy = api<decltype(&MaaImageBufferDestroy)>("MaaImageBufferDestroy");
    if (!controller || !post || !wait || !make || !cached || !encoded || !size || !destroy ||
        wait(controller,post(controller)) != MaaStatus_Succeeded) { error(env,"Screenshot failed"); return nullptr; }
    auto image = make();
    if (!image) { error(env,"Original frame allocation failed"); return nullptr; }
    if (!cached(controller,image)) { destroy(image); error(env,"Original frame unavailable"); return nullptr; }
    auto length = size(image);
    if (length == 0 || length > 32*1024*1024) { destroy(image); error(env,"Original frame size invalid"); return nullptr; }
    auto result = env->NewByteArray(static_cast<jsize>(length));
    if (!result) { destroy(image); return nullptr; }
    env->SetByteArrayRegion(result,0,static_cast<jsize>(length),reinterpret_cast<const jbyte*>(encoded(image)));
    destroy(image); return result;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_al1s_terminal_maa_NativeMaa_input(JNIEnv* env, jobject, jlong handle, jstring method, jintArray values) {
    std::lock_guard<std::mutex> lock(mutex);
    auto controller = reinterpret_cast<MaaController*>(handle);
    if (!controller) { error(env,"Controller unavailable"); return false; }
    auto wait = api<decltype(&MaaControllerWait)>("MaaControllerWait");
    auto click = api<decltype(&MaaControllerPostClick)>("MaaControllerPostClick");
    auto swipe = api<decltype(&MaaControllerPostSwipe)>("MaaControllerPostSwipe");
    auto key = api<decltype(&MaaControllerPostPressKey)>("MaaControllerPostPressKey");
    if (!wait || !click || !swipe || !key) { error(env,"Native input API missing"); return false; }
    int length = env->GetArrayLength(values); jint data[5] = {};
    if (length > 5) { error(env,"Input arguments invalid"); return false; }
    env->GetIntArrayRegion(values,0,length,data);
    auto operation = text(env,method); MaaCtrlId id = 0;
    if (operation == "click" && length == 2) id = click(controller,data[0],data[1]);
    else if (operation == "swipe" && length == 5) id = swipe(controller,data[0],data[1],data[2],data[3],data[4]);
    else if (operation == "key" && length == 1) id = key(controller,data[0]);
    else { error(env,"Input operation unsupported"); return false; }
    return wait && wait(controller,id) == MaaStatus_Succeeded;
}

extern "C" JNIEXPORT void JNICALL
Java_com_al1s_terminal_maa_NativeMaa_destroy(JNIEnv*, jobject, jlong handle) {
    std::lock_guard<std::mutex> lock(mutex);
    auto destroy = api<decltype(&MaaControllerDestroy)>("MaaControllerDestroy");
    if (handle && destroy) destroy(reinterpret_cast<MaaController*>(handle));
}
