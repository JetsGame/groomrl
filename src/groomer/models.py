from groomer.GroomEnv import GroomEnv, GroomEnvDual
import numpy as np

from groomer.tools import get_window_width, mass
from groomer.DQNAgentGroom import DQNAgentGroom
from groomer.JetTree import LundCoordinates
from groomer.read_data import Jets
from rl.policy import BoltzmannQPolicy, EpsGreedyQPolicy
from rl.memory import SequentialMemory

from keras.models import Sequential
from keras.layers import Dense, Activation, Flatten, LSTM, Dropout
from keras.optimizers import Adam, SGD, RMSprop, Adagrad
from keras.callbacks import TensorBoard

from hyperopt import STATUS_OK
from time import time

import pprint, json

#----------------------------------------------------------------------
def build_model(hps, input_dim):
    """Construct the underlying model used by the DQN."""
    model = Sequential()
    if hps['architecture']=='Dense':
        model.add(Flatten(input_shape=(1,) + input_dim))
        for i in range(hps['nb_layers']):
            model.add(Dense(hps['nb_units']))
            model.add(Activation('relu'))
        if hps['dropout']>0.0:
            model.add(Dropout(hps['dropout']))
        model.add(Dense(hps['nb_actions']))
        model.add(Activation('linear'))
    elif hps['architecture']=='LSTM':
        model.add(LSTM(hps['nb_units'], input_shape = (1,max(input_dim))))
        if hps['dropout']>0.0:
            model.add(Dropout(hps['dropout']))
        model.add(Dense(hps['nb_actions']))
        model.add(Activation('linear'))
    print(model.summary())
    return model

#----------------------------------------------------------------------
# inspire from:
# https://github.com/keras-rl/keras-rl/blob/master/examples/dqn_atari.py
def build_dqn(hps, input_dim):
    """Create a DQN agent to be used on lund inputs."""

    print('[+] Constructing DQN agent, model setup:')
    pprint.pprint(hps)

    # set up the DQN agent
    model = build_model(hps, input_dim)
    memory = SequentialMemory(limit=500000, window_length=1)
    if hps["policy"]=="boltzmann":
        policy = BoltzmannQPolicy()
    elif hps["policy"]=="epsgreedyq":
        policy = EpsGreedyQPolicy()
    else:
        raise ValueError("Invalid policy: %s"%hps["policy"])
    duelnet = hps["enable_dueling_network"]
    doubdqn = hps["enable_double_dqn"]
    agent = DQNAgentGroom(model=model, nb_actions=2,
                          enable_dueling_network=duelnet,
                          enable_double_dqn=doubdqn,
                          memory=memory, nb_steps_warmup=500,
                          target_model_update=1e-2, policy=policy)

    if hps['optimizer'] == 'Adam':
        opt = Adam(lr=hps['learning_rate'])
    elif hps['optimizer']  == 'SGD':
        opt = SGD(lr=hps['learning_rate'])
    elif hps['optimizer'] == 'RMSprop':
        opt = RMSprop(lr=hps['learning_rate'])
    elif hps['optimizer'] == 'Adagrad':
        opt = Adagrad(lr=hps['learning_rate'])

    agent.compile(opt, metrics=['mae'])

    return agent

#----------------------------------------------------------------------
def load_runcard(runcard):
    """Read in a runcard json file and set up dimensions correctly."""
    with open(runcard,'r') as f:
        res = json.load(f)
    # if there is a state_dim variable, set up LundCoordinates accordingly
    env_setup = res.get("groomer_env")
    LundCoordinates.change_dimension(env_setup["state_dim"])
    return res

#----------------------------------------------------------------------
def build_and_train_model(groomer_agent_setup):
    """Run a test model"""
    env_setup = groomer_agent_setup.get('groomer_env')
    if env_setup["dual_groomer_env"]:
        groomer_env = GroomEnvDual(env_setup, low=LundCoordinates.low,
                                   high=LundCoordinates.high)
    else:
        groomer_env = GroomEnv(env_setup, low=LundCoordinates.low,
                               high=LundCoordinates.high)

    agent_setup = groomer_agent_setup.get('groomer_agent')
    dqn = build_dqn(agent_setup, groomer_env.observation_space.shape)

    logdir = '%s/logs/{}'.format(time()) % groomer_agent_setup['output']
    print(f'[+] Constructing tensorboard log in {logdir}')
    tensorboard = TensorBoard(log_dir=logdir)

    print('[+] Fitting DQN agent...')
    r = dqn.fit(groomer_env, nb_steps=agent_setup['nstep'],
                visualize=False, verbose=1, callbacks=[tensorboard])

    # After training is done, we save the final weights.
    weight_file = '%s/weights.h5' % groomer_agent_setup['output']
    print(f'[+] Saving weights to {weight_file}')
    dqn.save_weights(weight_file, overwrite=True)

    # save the model architecture in json
    model_file = '%s/model.json' % groomer_agent_setup['output']
    print(f'[+] Saving model to {model_file}')
    with open(model_file, 'w') as outfile:
        json.dump(dqn.model.to_json(), outfile)

    # compute nominal reward after training
    median_reward = np.median(r.history['episode_reward'])
    print(f'[+] Median reward: {median_reward}')

    if groomer_agent_setup['scan']:
        # compute a metric for training set (TODO: change to validation)
        groomed_jets = []
        print(env_setup['val'])
        reader = Jets(env_setup['val'], env_setup['nev']) # load validation set
        for jet in reader.values():
            print('ciao')
            groomed_jets.append(dqn.groomer()(jet))
        masses = np.array(mass(groomed_jets))
        lower, upper, median = get_window_width(masses)
        loss = abs(upper-lower)/5 + abs(median-env_setup['mass'])
        print(f'Loss function for scan = {loss}')
        res = {'loss': loss, 'reward': median_reward, 'window': (lower,upper,median),
               'status': STATUS_OK}
    else:
        res = dqn
    return res
