
import time
import lcm
import MahonyAHRS
import numpy as np
from math import atan2, asin, cos, sin
from threading import Thread
from crazyflie_t import crazyflie_state_estimate_t
from vicon_t import vicon_pos_t


class SensorFusion():

	def __init__(self, listen_to_vicon=False, publish_to_lcm=False, use_rpydot=False):
		
		self.q = [1.0, 0.0, 0.0, 0.0] # quaternion of sensor frame relative to auxiliary frame
		self.integralFB = [0.0, 0.0, 0.0] # integral error terms scaled by Ki	
		self._last_rpy = [0.0, 0.0, 0.0]
		self._last_gyro = [0.0, 0.0, 0.0]
		self._last_imu_update = time.time()	

		self._last_xyz = [0.0, 0.0, 0.0]
		self._last_dxyz = [0.0, 0.0, 0.0]
		self._last_vicon_update = time.time()
		self._valid_vicon = False
		self._vicon_alpha = .8

		self._use_rpydot = use_rpydot
		self._publish_to_lcm = publish_to_lcm
		if publish_to_lcm:
			self._xhat_lc = lcm.LCM()

		if listen_to_vicon:
			Thread(target=self._vicon_listener).start()

	def add_imu_reading(self, imu_reading):
		(gx, gy, gz, ax, ay, az, dt_imu) = imu_reading

		dt = time.time() - self._last_imu_update
		new_quat = MahonyAHRS.MahonyAHRSupdateIMU(gx,gy,gz,ax,ay,az,dt,
												  self.q[0],self.q[1],self.q[2],self.q[3],
												  self.integralFB[0],self.integralFB[0],self.integralFB[0])
		self._last_imu_update = time.time()

		self.q = new_quat[0:4]
		self.integralFB = new_quat[4:]
		try:
			self._last_rpy = quat2rpy(self.q)
		except ValueError:
			pass
		self._last_gyro = [gx,gy,gz]

	def _vicon_listener(self):
		_vicon_listener_lc = lcm.LCM()
		_vicon_listener_lc.subscribe('crazyflie2_squ_ext',self._add_vicon_reading)
		while True:
			_vicon_listener_lc.handle()

	def _add_vicon_reading(self, channel, data):
		msg = vicon_pos_t.decode(data)
		
		if msg.q[0] < -999:
			self._valid_vicon = False
			self._last_dxyz = [0.0, 0.0, 0.0]
			return
		
		xyz = list(msg.q)[0:3]
		dxyz = [0.0, 0.0, 0.0]
		if self._valid_vicon:
			dt = 1.0/120.0
			dt_measured = (msg.timestamp-self._last_vicon_update)/1000.0
			if (dt_measured>1.1*dt):
				dt = dt_measured
			dxyz[0] = (1.0/dt)*(xyz[0]-self._last_xyz[0])
			dxyz[1] = (1.0/dt)*(xyz[1]-self._last_xyz[1])
			dxyz[2] = (1.0/dt)*(xyz[2]-self._last_xyz[2])
		
		self._last_xyz = xyz
		self._last_dxyz[0] = self._vicon_alpha*dxyz[0]+(1-self._vicon_alpha)*self._last_dxyz[0]
		self._last_dxyz[1] = self._vicon_alpha*dxyz[1]+(1-self._vicon_alpha)*self._last_dxyz[1]
		self._last_dxyz[2] = self._vicon_alpha*dxyz[2]+(1-self._vicon_alpha)*self._last_dxyz[2]
		self._last_vicon_update = msg.timestamp
		self._valid_vicon = True

	def get_xhat(self):
		# should maybe put a lock on those variables before accessing them

		if self._use_rpydot:
			try:
				angular_rate = angularvel2rpydot(self._last_rpy, body2world(self._last_rpy, self._last_gyro))
			except ValueError:
				angular_rate = [0.0, 0.0, 0.0]
		else:
			angular_rate = self._last_gyro

		xhat = [self._last_xyz[0],self._last_xyz[1],self._last_xyz[2],
				self._last_rpy[0],self._last_rpy[1],self._last_rpy[2],
				self._last_dxyz[0],self._last_dxyz[1],self._last_dxyz[2],
				angular_rate[0],angular_rate[1],angular_rate[2]]

		if self._publish_to_lcm:
			msg = crazyflie_state_estimate_t()
			msg.xhat = xhat
			self._xhat_lc.publish("crazyflie_state_estimate", msg.encode())

		return xhat


def quat2rpy(q):
	q_norm = (q[0]**2+q[1]**2+q[2]**2+q[3]**2)
	q = [q_i/q_norm for q_i in q]
	w = q[0]
	x = q[1]
	y = q[2]
	z = q[3]
	rpy = [atan2(2*(w*x + y*z), w*w + z*z - (x*x + y*y)),
  		   asin(2*(w*y - z*x)),
           atan2(2*(w*z + x*y), w*w + x*x - (y*y + z*z))]
	return rpy

def rotx(theta):
    c = cos(theta)
    s = sin(theta)
    M = np.matrix([[1,0,0],[0,c,-s],[0,s,c]])
    return M

def roty(theta):
    c = cos(-theta)
    s = sin(-theta)
    M = np.matrix([[c,0,-s],[0,1,0],[s,0,c]])
    return M

def rotz(theta):
    c = cos(theta)
    s = sin(theta)
    M = np.matrix([[c,-s,0],[s,c,0],[0,0,1]])
    return M

def rpy2rotmat(rpy):
    R = np.dot(rotz(rpy[2]),np.dot(roty(rpy[1]),rotx(rpy[0])))
    return R

def body2world(rpy, xyz):
    R = rpy2rotmat(rpy)
    xyz_world = np.dot(np.linalg.inv(R),np.array(xyz).transpose())
    return (np.array(xyz_world)[0]).tolist()

def angularvel2rpydot(rpy, omega):
	p = rpy[1]
	y = rpy[2]
	sy = sin(y)
	cy = cos(y)
	sp = sin(p)
	cp = cos(p)
	tp = sp/cp
	Phi = np.matrix([[cy/cp, sy/cp, 0],[-sy, cy, 0],[cy*tp, tp*sy, 1]])
	rpydot = np.dot(Phi,np.array(omega).transpose())
	return (np.array(rpydot)[0]).tolist()