#include <jni.h>
#include <cstdint>
#include <cstdlib>
#include <mutex>

// Public external ABI from MaaFramework e6aa89259ff6907197becebdeb8efc0074f13dfc.
// Maa's control unit consumes BGR bytes and zero indicates successful dispatch.
struct FrameInfo { uint32_t width, height, stride, length; void* data; void* frame_ref; };
struct Position { int x, y; };
struct StartGameArgs { const char* package_name; int force_stop; };
union ArgUnion { StartGameArgs start_game; const char* text; Position touch; int key_code; };
struct MethodParam { int display_id; int method; ArgUnion args; };
static_assert(sizeof(FrameInfo) == 32 && sizeof(MethodParam) == 24, "Maa arm64 external ABI");

namespace {
JavaVM* vm = nullptr;
jobject adapter = nullptr;
jmethodID capture = nullptr, dispatch = nullptr;
jfieldID width = nullptr, height = nullptr, pixels = nullptr;
std::mutex device_mutex;
class Environment {
public:
    JNIEnv* env = nullptr;
    bool attached = false;
    Environment() {
        if (!vm) return;
        if (vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) == JNI_EDETACHED)
            attached = vm->AttachCurrentThread(&env, nullptr) == JNI_OK;
    }
    ~Environment() { if (attached) vm->DetachCurrentThread(); }
};
bool failed(JNIEnv* env) {
    if (!env->ExceptionCheck()) return false;
    env->ExceptionClear(); return true;
}
}

extern "C" JNIEXPORT void JNICALL
Java_com_al1s_terminal_maa_NativeMaa_bindDevice(JNIEnv* env, jobject, jobject device) {
    std::lock_guard<std::mutex> lock(device_mutex);
    env->GetJavaVM(&vm);
    auto type = env->GetObjectClass(device);
    capture = env->GetMethodID(type, "capture", "()Lcom/al1s/terminal/device/NativePixelFrame;");
    dispatch = env->GetMethodID(type, "dispatch", "(IIILjava/lang/String;Z)I");
    auto frame = env->FindClass("com/al1s/terminal/device/NativePixelFrame");
    width = env->GetFieldID(frame, "width", "I"); height = env->GetFieldID(frame, "height", "I");
    pixels = env->GetFieldID(frame, "bgr", "[B");
    if (adapter) env->DeleteGlobalRef(adapter);
    adapter = env->NewGlobalRef(device);
    env->DeleteLocalRef(type); env->DeleteLocalRef(frame);
}

extern "C" __attribute__((visibility("default"))) FrameInfo GetLockedPixels() {
    std::lock_guard<std::mutex> lock(device_mutex);
    Environment scope;
    auto env = scope.env;
    if (!env || !adapter || !capture) return {};
    auto frame = env->CallObjectMethod(adapter, capture);
    if (failed(env) || !frame) return {};
    auto bytes = static_cast<jbyteArray>(env->GetObjectField(frame, pixels));
    int w = env->GetIntField(frame, width), h = env->GetIntField(frame, height);
    const int64_t size = static_cast<int64_t>(w) * h * 3;
    FrameInfo result {};
    if (bytes && w > 0 && h > 0 && size <= 32*1024*1024 && env->GetArrayLength(bytes) == size) {
        auto data = std::malloc(static_cast<size_t>(size));
        if (data) {
            env->GetByteArrayRegion(bytes, 0, static_cast<jsize>(size), static_cast<jbyte*>(data));
            if (!failed(env)) result = {static_cast<uint32_t>(w), static_cast<uint32_t>(h),
                static_cast<uint32_t>(w*3), static_cast<uint32_t>(size), data, data};
            else std::free(data);
        }
    }
    if (bytes) env->DeleteLocalRef(bytes);
    env->DeleteLocalRef(frame); return result;
}

extern "C" __attribute__((visibility("default"))) int UnlockPixels(FrameInfo frame) {
    std::free(frame.frame_ref); return 0;
}

extern "C" __attribute__((visibility("default"))) int DispatchInputMessage(MethodParam param) {
    std::lock_guard<std::mutex> lock(device_mutex);
    Environment scope;
    auto env = scope.env;
    if (!env || !adapter || !dispatch || param.display_id != 0) return -1;
    int x = 0, y = 0; const char* value = nullptr; bool force_stop = false;
    switch (param.method) {
        case 1: value = param.args.start_game.package_name; force_stop = param.args.start_game.force_stop != 0; break;
        case 2: case 4: value = param.args.text; break;
        case 6: case 7: case 8: x = param.args.touch.x; y = param.args.touch.y; break;
        case 9: case 10: x = param.args.key_code; break;
        default: return -1;
    }
    auto string = value ? env->NewStringUTF(value) : nullptr;
    int result = env->CallIntMethod(adapter, dispatch, param.method, x, y, string, force_stop);
    if (string) env->DeleteLocalRef(string);
    return failed(env) ? -1 : result;
}
