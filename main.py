import os
import sys
import numpy as np
import tensorflow as tf
from tqdm import tqdm

from config import cfg
from utils import load_data
from capsNet import CapsNet

import pdb
import csv
import pandas as pd


def save_to():
    if not os.path.exists(cfg.results):
        os.mkdir(cfg.results)
    if cfg.is_training:
        loss = cfg.results + '/loss.csv'
        train_acc = cfg.results + '/train_acc.csv'
        val_acc = cfg.results + '/val_acc.csv'

        if os.path.exists(val_acc):
            os.remove(val_acc)
        if os.path.exists(loss):
            os.remove(loss)
        if os.path.exists(train_acc):
            os.remove(train_acc)

        fd_train_acc = open(train_acc, 'w')
        fd_train_acc.write('step,train_acc\n')
        fd_loss = open(loss, 'w')
        fd_loss.write('step,loss\n')
        fd_val_acc = open(val_acc, 'w')
        fd_val_acc.write('step,val_acc\n')
        return(fd_train_acc, fd_loss, fd_val_acc)
    else:
        test_acc = cfg.results + '/test_acc.csv'
        if os.path.exists(test_acc):
            os.remove(test_acc)
        fd_test_acc = open(test_acc, 'w')
        fd_test_acc.write('test_acc\n')
        return(fd_test_acc)


def train(model, supervisor, num_label):
    trX, trY, num_tr_batch, valX, valY, num_val_batch = load_data(cfg.dataset, cfg.batch_size, is_training=True)
    if cfg.num_batch is not None:
        num_tr_batch = cfg.num_batch
    
    Y = valY[:num_val_batch * cfg.batch_size].reshape((-1, 1))

    fd_train_acc, fd_loss, fd_val_acc = save_to()
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    with supervisor.managed_session(config=config) as sess:
        print("\nNote: all of results will be saved to directory: " + cfg.results)
        for epoch in range(cfg.epoch):
            sys.stdout.write('Training for epoch ' + str(epoch) + '/' + str(cfg.epoch) + ':')
            sys.stdout.flush()
            if supervisor.should_stop():
                print('supervisor stoped!')
                break
            for step in tqdm(range(num_tr_batch), total=num_tr_batch, ncols=70, leave=False, unit='b'):
                start = step * cfg.batch_size
                end = start + cfg.batch_size
                global_step = epoch * num_tr_batch + step

                if global_step % cfg.train_sum_freq == 0:
                    _, loss, train_acc, summary_str = sess.run([model.train_op, model.total_loss, model.accuracy, model.train_summary])
                    assert not np.isnan(loss), 'Something wrong! loss is nan...'
                    supervisor.summary_writer.add_summary(summary_str, global_step)

                    fd_loss.write(str(global_step) + ',' + str(loss) + "\n")
                    fd_loss.flush()
                    fd_train_acc.write(str(global_step) + ',' + str(train_acc / cfg.batch_size) + "\n")
                    fd_train_acc.flush()
                else:
                    sess.run(model.train_op)

                if cfg.val_sum_freq != 0 and (global_step) % cfg.val_sum_freq == 0:
                    val_acc = 0
                    for i in range(num_val_batch):
                        start = i * cfg.batch_size
                        end = start + cfg.batch_size
                        acc = sess.run(model.accuracy, {model.X: valX[start:end], model.labels: valY[start:end]})
                        val_acc += acc
                    val_acc = val_acc / (cfg.batch_size * num_val_batch)
                    fd_val_acc.write(str(global_step) + ',' + str(val_acc) + '\n')
                    fd_val_acc.flush()

            if (epoch + 1) % cfg.save_freq == 0:
                supervisor.saver.save(sess, cfg.logdir + '/model_epoch_%04d_step_%02d' % (epoch, global_step))

        fd_val_acc.close()
        fd_train_acc.close()
        fd_loss.close()


def evaluation(model, supervisor, num_label):
    teX, teY, num_te_batch = load_data(cfg.dataset, cfg.batch_size, is_training=False)
    if cfg.num_batch is not None:
        num_te_batch = cfg.num_batch
    
    #create the record table
    act_table_name = 'routing.csv'
    df0 = pd.DataFrame([], columns=['label', 'prediction'] + ['l_'+str(i+1) for i in range(32)] + ['inputId'] + ['rotation'] +\
                                   ['cosine_'+str(i+1) for i in range(32)] + ['contribution_'+str(i+1) for i in range(32)] +\
                                   ['cosineRef_'+str(i+1) for i in range(32)]
                      )
    df0.to_csv(act_table_name, index=False)
    
    fd_test_acc = save_to()
    with supervisor.managed_session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
        supervisor.saver.restore(sess, tf.train.latest_checkpoint(cfg.logdir))
        tf.logging.info('Model restored!')

        test_acc = 0
        
        #use the first batch only
        start = 0 * cfg.batch_size
        end = start + cfg.batch_size
        
        #fist without rotation
        radian = np.zeros((cfg.batch_size,))
        res = sess.run([model.c_IJ, model.argmax_idx, model.u_hat], 
                       {model.origX: teX[start:end], model.labels: teY[start:end], model.radian: radian})
        c_IJ = res[0]
        argmax_idx = res[1] #(None,)
        u_hat = res[2]
        
        
        c_I = c_IJ[range(cfg.batch_size), :, argmax_idx].reshape([cfg.batch_size, -1, 32]) #(128, 36, 32)
        max_inds = np.argmax(c_I, axis=1) #(None, 32)
        temp_inds = np.indices((cfg.batch_size, 32))
        inds = (temp_inds[0], max_inds, temp_inds[1])
        
        u_hat_I = u_hat[range(cfg.batch_size), :, argmax_idx].reshape([cfg.batch_size, -1, 32, 16]) #(128, 36, 32, 16)
        u_hat_max = u_hat_I[inds]
        
        u_hat_max_ref = u_hat_max
        norm_ref = np.linalg.norm(u_hat_max_ref, axis=2)
    
        
        for i in tqdm(range(num_te_batch), total=num_te_batch, ncols=70, leave=False, unit='b'):
            #start = i * cfg.batch_size
            #end = start + cfg.batch_size
            
            if i==0:
                radian = np.zeros((cfg.batch_size,))
            else:
                radian = np.random.uniform(-20, 20, size=(cfg.batch_size,))/180*np.pi
            
            res = sess.run([model.accuracy, model.c_IJ, model.argmax_idx,
                            model.cosines, model.contributions, model.u_hat], 
                           {model.origX: teX[start:end], model.labels: teY[start:end], model.radian: radian})
            acc = res[0]
            c_IJ = res[1]
            argmax_idx = res[2] #(None,)
            cosines_IJ = res[3] #(None, 1152, 10, 1, 1)
            contributions_IJ = res[4]
            u_hat = res[5]
            
            c_I = c_IJ[range(cfg.batch_size), :, argmax_idx].reshape([cfg.batch_size, -1, 32]) #(128, 36, 32)
            #c_I.shape = [batch_size, 36, 32]
            #layer_I = np.max(c_I, axis=1)
            max_inds = np.argmax(c_I, axis=1) #(None, 32)
            temp_inds = np.indices((cfg.batch_size, 32))
            inds = (temp_inds[0], max_inds, temp_inds[1])
            
            layer_I = c_I[inds]
            
            #for cosines
            cosines_I = cosines_IJ[range(cfg.batch_size), :, argmax_idx].reshape([cfg.batch_size, -1, 32]) #(None, 36, 32)
            cosines = cosines_I[inds]
            
            #for contributions
            contributions_I = contributions_IJ[range(cfg.batch_size), :, argmax_idx].reshape([cfg.batch_size, -1, 32])
            contributions = contributions_I[inds]
            
            #for cosines_ref
            u_hat_I = u_hat[range(cfg.batch_size), :, argmax_idx].reshape([cfg.batch_size, -1, 32, 16]) #(128, 36, 32, 16)
            u_hat_max = u_hat_I[inds]
            
            norm = np.linalg.norm(u_hat_max, axis=2)
            
            prod = np.sum(np.multiply(u_hat_max, u_hat_max_ref), axis=2)
            
            cosines_ref = prod/norm/norm_ref
            
            
            
            df = np.concatenate((np.array([teY[start:end]]).T,
                                 np.array([argmax_idx]).T,
                                 layer_I,
                                 np.arange(cfg.batch_size).reshape((-1,1)),
                                 radian.reshape((-1,1)),
                                 cosines,
                                 contributions,
                                 cosines_ref
                                ), axis=1)
            
            test_acc += acc
            
            #append record to csv
            df = pd.DataFrame(df)
            df.to_csv(act_table_name, mode='a', header=False, index=False)
            
        test_acc = test_acc / (cfg.batch_size * num_te_batch)
        fd_test_acc.write(str(test_acc))
        fd_test_acc.close()
        print('Test accuracy has been saved to ' + cfg.results + '/test_accuracy.txt')


def main(_):
    tf.logging.info(' Loading Graph...')
    num_label = 10
    model = CapsNet()
    tf.logging.info(' Graph loaded')

    sv = tf.train.Supervisor(graph=model.graph, logdir=cfg.logdir, save_model_secs=0)

    if cfg.is_training:
        tf.logging.info(' Start trainging...')
        train(model, sv, num_label)
        tf.logging.info('Training done')
    else:
        evaluation(model, sv, num_label)

if __name__ == "__main__":
    tf.app.run()
