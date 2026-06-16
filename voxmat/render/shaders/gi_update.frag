#version 330
// GI update pass: refresh one irradiance-atlas texel (one voxel face) per
// fragment. Each frame shoots a single cosine-weighted ray from the face,
// reads the hit voxel's cached outgoing radiance (one bounce) and blends it
// into the running average — light propagates one voxel-bounce per frame and
// converges to multi-bounce GI. The cache is view-independent.
out vec4 f_color;
in vec2 v_uv;
uniform int u_gi_frame;
#include "common.glsl"

void main() {
    ivec2 texel = ivec2(gl_FragCoord.xy);
    int Y = u_dims.y, YZ = u_dims.y * u_dims.z;
    int face = texel.y / YZ;
    int rem = texel.y - face * YZ;
    int z = rem / Y;
    ivec3 v = ivec3(texel.x, rem - z * Y, z);
    if (v.x >= u_dims.x || z >= u_dims.z || face >= 6) { f_color = vec4(0.0); return; }
    if (!filled(v)) { f_color = vec4(0.0, 0.0, 0.0, 1.0); return; }

    // Skip interior faces (neighbour is opaque solid) — never seen, receive no
    // light. A transmissive (glass) neighbour does NOT occlude: the face is still
    // lit through it, so it must be gathered or it renders black behind the glass.
    vec3 faceNrm = face_normal(face);
    ivec3 neighbour = v + ivec3(faceNrm);
    if (in_bounds(neighbour) && filled(neighbour) && transmission_at(neighbour) <= 0.0) {
        f_color = vec4(0.0, 0.0, 0.0, 1.0); return;
    }

    uint rng = uint(v.x) * 1973u + uint(v.y) * 9277u + uint(v.z) * 26699u
             + uint(face) * 40961u + uint(u_gi_frame) * 53653u + 1u;
    // cosine-weighted hemisphere sample around the face normal -> I = irradiance/pi
    vec3 dir = cosine_sample(faceNrm, rng);
    vec3 rayOrigin = vec3(v) + 0.5 + faceNrm * (0.5 + EPS);
    ivec3 hitCell; vec3 hitNormal; float hitDistance;
    vec3 sampled = trace_voxel(rayOrigin, dir, v, true, hitCell, hitNormal, hitDistance)
                 ? voxel_outgoing_radiance(hitCell, hitNormal)
                 : sample_env(dir);

    vec3 prev = texelFetch(u_gi, texel, 0).rgb;
    float alpha = 1.0 / float(u_gi_frame + 1);
    f_color = vec4(mix(prev, sampled, alpha), 1.0);
}
